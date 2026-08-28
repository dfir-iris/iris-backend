#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 3 of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
#  Lesser General Public License for more details.
#
#  You should have received a copy of the GNU Lesser General Public License
#  along with this program; if not, write to the Free Software Foundation,
#  Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.

"""Unit tests for pure validation logic in app.schema.marshables.

Only schemas whose .load() path does NOT touch the database are
exercised here — those with field-level validate= constraints or
@pre_load / @validates_schema hooks that run purely in-memory.

Schemas covered
---------------
* BannerSchema            — text Length, purpose OneOf, timespan ordering
* TaskLogSchema           — log_content min-length (pure Schema)
* CaseAddNoteSchema       — note_title Length (pure Schema)
* CaseTemplateSchema      — author/title_prefix Length, tasks field validator
* _validate_condition_node — recursive condition-tree helper
* _validate_rule_conditions — rule-conditions DSL validator
"""

import datetime
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..', 'source'))

import unittest

# ---------------------------------------------------------------------------
# Bootstrap the Flask app (no live DB required; the connection errors at
# startup are non-fatal and happen before app_context() is entered)
# ---------------------------------------------------------------------------
os.environ.setdefault('POSTGRES_SERVER', 'localhost')

from app import app


def _ctx():
    """Return a pushed Flask application context."""
    ctx = app.app_context()
    ctx.push()
    return ctx


# Keep a single pushed context for the entire test run so that the
# SQLAlchemy / marshmallow-sqlalchemy machinery is initialised once.
_APP_CTX = _ctx()


from marshmallow.exceptions import ValidationError
from app.schema.marshables import (
    BannerSchema,
    TaskLogSchema,
    CaseAddNoteSchema,
    CaseTemplateSchema,
    _validate_condition_node,
    _validate_rule_conditions,
)


# ---------------------------------------------------------------------------
# BannerSchema
# ---------------------------------------------------------------------------

class TestBannerSchemaTextValidation(unittest.TestCase):
    """Tests for the 'text' field (Length min=1, max=2000)."""

    def setUp(self):
        self.schema = BannerSchema()

    def _minimal(self, **overrides):
        data = {'text': 'Test banner', 'purpose': 'info'}
        data.update(overrides)
        return data

    def test_valid_minimal_payload_loads(self):
        result = self.schema.load(self._minimal())
        self.assertIsNotNone(result)

    def test_missing_text_raises(self):
        with self.assertRaises(ValidationError) as ctx:
            self.schema.load({'purpose': 'info'})
        self.assertIn('text', ctx.exception.messages)

    def test_empty_text_raises(self):
        with self.assertRaises(ValidationError) as ctx:
            self.schema.load(self._minimal(text=''))
        self.assertIn('text', ctx.exception.messages)

    def test_text_at_max_length_loads(self):
        result = self.schema.load(self._minimal(text='x' * 2000))
        self.assertIsNotNone(result)

    def test_text_exceeding_max_length_raises(self):
        with self.assertRaises(ValidationError) as ctx:
            self.schema.load(self._minimal(text='x' * 2001))
        self.assertIn('text', ctx.exception.messages)


class TestBannerSchemaPurposeValidation(unittest.TestCase):
    """Tests for the 'purpose' field (OneOf info/warning/error)."""

    def setUp(self):
        self.schema = BannerSchema()

    def _minimal(self, **overrides):
        data = {'text': 'hi', 'purpose': 'info'}
        data.update(overrides)
        return data

    def test_purpose_info_loads(self):
        self.assertIsNotNone(self.schema.load(self._minimal(purpose='info')))

    def test_purpose_warning_loads(self):
        self.assertIsNotNone(self.schema.load(self._minimal(purpose='warning')))

    def test_purpose_error_loads(self):
        self.assertIsNotNone(self.schema.load(self._minimal(purpose='error')))

    def test_missing_purpose_raises(self):
        with self.assertRaises(ValidationError) as ctx:
            self.schema.load({'text': 'hi'})
        self.assertIn('purpose', ctx.exception.messages)

    def test_invalid_purpose_raises(self):
        with self.assertRaises(ValidationError) as ctx:
            self.schema.load(self._minimal(purpose='critical'))
        self.assertIn('purpose', ctx.exception.messages)


class TestBannerSchemaTimespanValidation(unittest.TestCase):
    """Tests for the timespan (@validates_schema and @pre_load blank coercion)."""

    def setUp(self):
        self.schema = BannerSchema()

    def _minimal(self, **overrides):
        data = {'text': 'hi', 'purpose': 'info'}
        data.update(overrides)
        return data

    def _iso(self, dt):
        return dt.isoformat()

    def _dt(self, year, month, day):
        return datetime.datetime(year, month, day, tzinfo=datetime.timezone.utc)

    def test_blank_start_at_coerced_to_none(self):
        result = self.schema.load(self._minimal(start_at=''))
        self.assertIsNone(result.start_at)

    def test_blank_end_at_coerced_to_none(self):
        result = self.schema.load(self._minimal(end_at=''))
        self.assertIsNone(result.end_at)

    def test_valid_timespan_loads(self):
        result = self.schema.load(self._minimal(
            start_at=self._iso(self._dt(2024, 1, 1)),
            end_at=self._iso(self._dt(2024, 1, 2)),
        ))
        self.assertIsNotNone(result)

    def test_end_before_start_raises(self):
        with self.assertRaises(ValidationError) as ctx:
            self.schema.load(self._minimal(
                start_at=self._iso(self._dt(2024, 1, 2)),
                end_at=self._iso(self._dt(2024, 1, 1)),
            ))
        self.assertIn('end_at', ctx.exception.messages)

    def test_equal_start_and_end_raises(self):
        # end_at must be *strictly* after start_at
        ts = self._iso(self._dt(2024, 6, 15))
        with self.assertRaises(ValidationError) as ctx:
            self.schema.load(self._minimal(start_at=ts, end_at=ts))
        self.assertIn('end_at', ctx.exception.messages)

    def test_dismissable_defaults_to_true(self):
        result = self.schema.load(self._minimal())
        self.assertTrue(result.dismissable)

    def test_dismissable_can_be_set_false(self):
        result = self.schema.load(self._minimal(dismissable=False))
        self.assertFalse(result.dismissable)


# ---------------------------------------------------------------------------
# TaskLogSchema
# ---------------------------------------------------------------------------

class TestTaskLogSchema(unittest.TestCase):
    """Tests for TaskLogSchema (pure marshmallow Schema, no ORM)."""

    def setUp(self):
        self.schema = TaskLogSchema()

    def test_valid_log_content_loads(self):
        result = self.schema.load({'log_content': 'Something happened'})
        self.assertEqual(result['log_content'], 'Something happened')

    def test_empty_log_content_raises(self):
        with self.assertRaises(ValidationError) as ctx:
            self.schema.load({'log_content': ''})
        self.assertIn('log_content', ctx.exception.messages)

    def test_missing_log_content_is_optional(self):
        # log_content is not required=True, so omitting it should work
        result = self.schema.load({})
        self.assertNotIn('log_content', result)

    def test_whitespace_only_log_content_is_length_1_or_more(self):
        # A single space satisfies min=1
        result = self.schema.load({'log_content': ' '})
        self.assertEqual(result['log_content'], ' ')


# ---------------------------------------------------------------------------
# CaseAddNoteSchema
# ---------------------------------------------------------------------------

class TestCaseAddNoteSchema(unittest.TestCase):
    """Tests for CaseAddNoteSchema note_title Length(min=1, max=154)."""

    def setUp(self):
        self.schema = CaseAddNoteSchema()

    def test_valid_title_loads(self):
        result = self.schema.load({'note_title': 'My note'})
        self.assertEqual(result['note_title'], 'My note')

    def test_missing_title_raises(self):
        with self.assertRaises(ValidationError) as ctx:
            self.schema.load({'note_content': 'content only'})
        self.assertIn('note_title', ctx.exception.messages)

    def test_empty_title_raises(self):
        with self.assertRaises(ValidationError) as ctx:
            self.schema.load({'note_title': ''})
        self.assertIn('note_title', ctx.exception.messages)

    def test_title_at_max_length_loads(self):
        result = self.schema.load({'note_title': 'a' * 154})
        self.assertEqual(len(result['note_title']), 154)

    def test_title_exceeding_max_length_raises(self):
        with self.assertRaises(ValidationError) as ctx:
            self.schema.load({'note_title': 'a' * 155})
        self.assertIn('note_title', ctx.exception.messages)

    def test_optional_note_content_included_when_provided(self):
        result = self.schema.load({'note_title': 'Title', 'note_content': 'Body'})
        self.assertEqual(result['note_content'], 'Body')

    def test_note_content_absent_when_not_provided(self):
        result = self.schema.load({'note_title': 'Title'})
        self.assertNotIn('note_content', result)


# ---------------------------------------------------------------------------
# CaseTemplateSchema
# ---------------------------------------------------------------------------

class TestCaseTemplateSchema(unittest.TestCase):
    """Tests for CaseTemplateSchema field validators (pure logic, no DB)."""

    def setUp(self):
        self.schema = CaseTemplateSchema()

    def _base(self, **overrides):
        data = {'name': 'My Template', 'created_by_user_id': 1}
        data.update(overrides)
        return data

    def test_valid_minimal_payload_loads(self):
        result = self.schema.load(self._base())
        self.assertEqual(result['name'], 'My Template')

    def test_missing_name_raises(self):
        with self.assertRaises(ValidationError) as ctx:
            self.schema.load({'created_by_user_id': 1})
        self.assertIn('name', ctx.exception.messages)

    def test_missing_created_by_user_id_raises(self):
        with self.assertRaises(ValidationError) as ctx:
            self.schema.load({'name': 'tmpl'})
        self.assertIn('created_by_user_id', ctx.exception.messages)

    def test_author_at_max_length_loads(self):
        result = self.schema.load(self._base(author='a' * 128))
        self.assertEqual(len(result['author']), 128)

    def test_author_exceeding_max_length_raises(self):
        with self.assertRaises(ValidationError) as ctx:
            self.schema.load(self._base(author='a' * 129))
        self.assertIn('author', ctx.exception.messages)

    def test_title_prefix_at_max_length_loads(self):
        result = self.schema.load(self._base(title_prefix='x' * 32))
        self.assertEqual(len(result['title_prefix']), 32)

    def test_title_prefix_exceeding_max_length_raises(self):
        with self.assertRaises(ValidationError) as ctx:
            self.schema.load(self._base(title_prefix='x' * 33))
        self.assertIn('title_prefix', ctx.exception.messages)

    def test_tasks_with_string_values_loads(self):
        result = self.schema.load(self._base(tasks=[{'title': 'Task A', 'description': 'Do it'}]))
        self.assertEqual(result['tasks'][0]['title'], 'Task A')

    def test_tasks_with_list_of_strings_loads(self):
        result = self.schema.load(self._base(tasks=[{'tags': ['t1', 't2']}]))
        self.assertEqual(result['tasks'][0]['tags'], ['t1', 't2'])

    def test_tasks_with_int_value_raises(self):
        with self.assertRaises(ValidationError):
            self.schema.load(self._base(tasks=[{'priority': 42}]))

    def test_tasks_defaults_to_empty_list_when_absent(self):
        result = self.schema.load(self._base())
        self.assertEqual(result['tasks'], [])

    def test_tags_list_of_strings_loads(self):
        result = self.schema.load(self._base(tags=['incident', 'malware']))
        self.assertEqual(result['tags'], ['incident', 'malware'])

    def test_note_directories_defaults_to_empty_list(self):
        result = self.schema.load(self._base())
        self.assertEqual(result['note_directories'], [])


# ---------------------------------------------------------------------------
# _validate_condition_node  (module-level helper, called by cluster rules
# and investigation flows)
# ---------------------------------------------------------------------------

class TestValidateConditionNode(unittest.TestCase):
    """Tests for the recursive _validate_condition_node helper."""

    def test_valid_leaf_node_passes(self):
        # Must not raise
        _validate_condition_node({'field': 'severity', 'operator': 'eq', 'value': 'high'}, 'root')

    def test_leaf_without_operator_raises(self):
        with self.assertRaises(ValidationError) as ctx:
            _validate_condition_node({'field': 'severity'}, 'root')
        self.assertIn('field and operator', str(ctx.exception))

    def test_leaf_without_field_raises(self):
        with self.assertRaises(ValidationError) as ctx:
            _validate_condition_node({'operator': 'eq'}, 'root')
        self.assertIn('field and operator', str(ctx.exception))

    def test_non_dict_node_raises(self):
        with self.assertRaises(ValidationError) as ctx:
            _validate_condition_node('not_a_dict', 'root')
        self.assertIn('must be an object', str(ctx.exception))

    def test_valid_group_node_passes(self):
        node = {
            'logic': 'and',
            'conditions': [
                {'field': 'status', 'operator': 'eq', 'value': 'open'},
            ]
        }
        _validate_condition_node(node, 'root')  # must not raise

    def test_group_node_with_invalid_logic_raises(self):
        node = {'logic': 'xor', 'conditions': [{'field': 'f', 'operator': 'eq'}]}
        with self.assertRaises(ValidationError) as ctx:
            _validate_condition_node(node, 'root')
        self.assertIn("'and'/'or'/'not'", str(ctx.exception))

    def test_group_node_conditions_not_list_raises(self):
        node = {'logic': 'and', 'conditions': 'not_a_list'}
        with self.assertRaises(ValidationError) as ctx:
            _validate_condition_node(node, 'root')
        self.assertIn('must be a list', str(ctx.exception))

    def test_nested_group_node_passes(self):
        node = {
            'logic': 'or',
            'conditions': [
                {'field': 'a', 'operator': 'eq'},
                {
                    'logic': 'and',
                    'conditions': [{'field': 'b', 'operator': 'ne'}],
                },
            ]
        }
        _validate_condition_node(node, 'root')  # must not raise

    def test_nested_group_with_bad_inner_node_raises(self):
        node = {
            'logic': 'and',
            'conditions': [
                {'logic': 'or', 'conditions': ['bad_leaf']},
            ]
        }
        with self.assertRaises(ValidationError):
            _validate_condition_node(node, 'root')


# ---------------------------------------------------------------------------
# _validate_rule_conditions  (top-level payload validator used by
# ClusterRuleSchema @pre_load)
# ---------------------------------------------------------------------------

class TestValidateRuleConditions(unittest.TestCase):
    """Tests for the _validate_rule_conditions helper."""

    def _valid(self):
        return {
            'logic': 'and',
            'conditions': [{'field': 'severity', 'operator': 'eq', 'value': 'high'}],
        }

    def test_valid_conditions_pass(self):
        _validate_rule_conditions(self._valid())  # must not raise

    def test_non_dict_raises(self):
        with self.assertRaises(ValidationError) as ctx:
            _validate_rule_conditions('not_a_dict')
        self.assertIn('must be an object', str(ctx.exception))

    def test_empty_conditions_list_raises(self):
        with self.assertRaises(ValidationError) as ctx:
            _validate_rule_conditions({'logic': 'and', 'conditions': []})
        self.assertIn('non-empty', str(ctx.exception))

    def test_conditions_not_list_raises(self):
        with self.assertRaises(ValidationError) as ctx:
            _validate_rule_conditions({'logic': 'and', 'conditions': 'not_a_list'})
        self.assertIn('non-empty', str(ctx.exception))

    def test_invalid_logic_raises(self):
        data = self._valid()
        data['logic'] = 'xor'
        with self.assertRaises(ValidationError) as ctx:
            _validate_rule_conditions(data)
        self.assertIn("'and'/'or'/'not'", str(ctx.exception))

    def test_valid_logic_or_passes(self):
        data = self._valid()
        data['logic'] = 'or'
        _validate_rule_conditions(data)  # must not raise

    def test_valid_logic_not_passes(self):
        data = self._valid()
        data['logic'] = 'not'
        _validate_rule_conditions(data)  # must not raise

    def test_negative_time_window_raises(self):
        data = self._valid()
        data['time_window_seconds'] = -1
        with self.assertRaises(ValidationError) as ctx:
            _validate_rule_conditions(data)
        self.assertIn('non-negative', str(ctx.exception))

    def test_zero_time_window_passes(self):
        data = self._valid()
        data['time_window_seconds'] = 0
        _validate_rule_conditions(data)  # must not raise

    def test_positive_time_window_passes(self):
        data = self._valid()
        data['time_window_seconds'] = 3600
        _validate_rule_conditions(data)  # must not raise

    def test_float_time_window_raises(self):
        data = self._valid()
        data['time_window_seconds'] = 1.5
        with self.assertRaises(ValidationError) as ctx:
            _validate_rule_conditions(data)
        self.assertIn('non-negative integer', str(ctx.exception))

    def test_group_by_as_list_of_strings_passes(self):
        data = self._valid()
        data['group_by'] = ['alert_customer_id', 'severity']
        _validate_rule_conditions(data)  # must not raise

    def test_group_by_as_string_raises(self):
        data = self._valid()
        data['group_by'] = 'alert_customer_id'
        with self.assertRaises(ValidationError) as ctx:
            _validate_rule_conditions(data)
        self.assertIn('list of field names', str(ctx.exception))

    def test_group_by_with_non_string_element_raises(self):
        data = self._valid()
        data['group_by'] = ['field_a', 42]
        with self.assertRaises(ValidationError) as ctx:
            _validate_rule_conditions(data)
        self.assertIn('list of field names', str(ctx.exception))

    def test_missing_conditions_key_raises(self):
        with self.assertRaises(ValidationError) as ctx:
            _validate_rule_conditions({'logic': 'and'})
        self.assertIn('non-empty', str(ctx.exception))

    def test_invalid_leaf_inside_conditions_raises(self):
        data = {
            'logic': 'and',
            'conditions': [{'operator': 'eq'}],  # missing 'field'
        }
        with self.assertRaises(ValidationError) as ctx:
            _validate_rule_conditions(data)
        self.assertIn('field and operator', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
