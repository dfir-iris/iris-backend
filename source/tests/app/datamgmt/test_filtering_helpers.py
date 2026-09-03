#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for build_condition and get_field_from_model in datamgmt/filtering.py.

build_condition returns SQLAlchemy clause elements; we test the operator
dispatch by calling the returned clause's compile() or by checking the
BinaryExpression type.  get_field_from_model is a plain attribute lookup
with a restricted-field guard.
"""

from unittest import TestCase
from unittest.mock import MagicMock, patch

from sqlalchemy import Column, Integer, String
from app.datamgmt.filtering import build_condition, get_field_from_model


# ---------------------------------------------------------------------------
# Helpers — tiny in-memory columns that satisfy hasattr(col, 'property')
# being falsy (plain Column objects don't have a .property the same way
# mapped attributes do, so the relationship-check branch is skipped).
# ---------------------------------------------------------------------------

_INT_COL = Column('severity', Integer)
_STR_COL = Column('name', String)


class TestBuildConditionScalarOperators(TestCase):

    def test_eq_returns_binary_expression(self):
        expr = build_condition(_INT_COL, 'eq', 3)
        self.assertIsNotNone(expr)

    def test_not_operator_returns_expression(self):
        expr = build_condition(_INT_COL, 'not', 3)
        self.assertIsNotNone(expr)

    def test_neq_operator_returns_expression(self):
        expr = build_condition(_INT_COL, 'neq', 3)
        self.assertIsNotNone(expr)

    def test_in_operator_returns_expression(self):
        expr = build_condition(_INT_COL, 'in', [1, 2, 3])
        self.assertIsNotNone(expr)

    def test_not_in_operator_returns_expression(self):
        expr = build_condition(_INT_COL, 'not_in', [1, 2])
        self.assertIsNotNone(expr)

    def test_like_operator_returns_expression(self):
        expr = build_condition(_STR_COL, 'like', 'foo')
        self.assertIsNotNone(expr)

    def test_not_like_operator_returns_expression(self):
        expr = build_condition(_STR_COL, 'not_like', 'foo')
        self.assertIsNotNone(expr)

    def test_unsupported_operator_raises_value_error(self):
        with self.assertRaises(ValueError):
            build_condition(_INT_COL, 'contains', 'x')


class TestBuildConditionOrderedComparisons(TestCase):
    """The rule builder offers '≥ greater or equal' / '≤ less or equal' on
    every field, but build_condition implemented neither — only
    build_json_condition did. Selecting one on any non-JSON field raised
    `ValueError: Unsupported operator: gte`, which surfaced as an empty
    rule preview or a 400 on alert filtering.
    """

    def test_gte_renders_a_greater_or_equal_comparison(self):
        expr = build_condition(_INT_COL, 'gte', 3)
        self.assertIn('>=', str(expr))

    def test_lte_renders_a_less_or_equal_comparison(self):
        expr = build_condition(_INT_COL, 'lte', 3)
        self.assertIn('<=', str(expr))

    def test_gte_works_on_a_text_column(self):
        # Lexicographic ordering is valid SQL for text — no cast required.
        expr = build_condition(_STR_COL, 'gte', 'm')
        self.assertIn('>=', str(expr))

    def test_gte_with_integer_value_is_not_cast(self):
        # When the caller already passes an int, the column type handles
        # binding — no CAST needed.
        expr = build_condition(_INT_COL, 'gte', 3)
        self.assertNotIn('CAST', str(expr).upper())

    def test_gte_coerces_string_value_to_int(self):
        # Regression: the frontend always stores condition values as JSON
        # strings (see `coerceValue` in ConditionsBuilder.svelte). SQLAlchemy
        # binds a Python str as `text`, and Postgres raises
        # "operator does not exist: integer >= text" — caught by
        # `_rule_matches_alert`'s broad except, so every `gte`/`lte` rule
        # against an integer column silently returned 0 matches.
        # The fix: coerce numeric-looking strings to int/float before binding.
        expr = build_condition(_INT_COL, 'gte', '0')
        self.assertIn('>=', str(expr))
        # The bound value must be the integer 0, not the string '0'.
        # Check via compiled params — a string bind would produce '0'::text.
        from sqlalchemy.dialects import postgresql
        compiled = expr.compile(dialect=postgresql.dialect())
        bound = list(compiled.params.values())[0]
        self.assertIsInstance(bound, int)

    def test_lte_coerces_string_value_to_int(self):
        expr = build_condition(_INT_COL, 'lte', '5')
        from sqlalchemy.dialects import postgresql
        compiled = expr.compile(dialect=postgresql.dialect())
        bound = list(compiled.params.values())[0]
        self.assertIsInstance(bound, int)

    def test_gte_coerces_float_string(self):
        expr = build_condition(_INT_COL, 'gte', '3.14')
        from sqlalchemy.dialects import postgresql
        compiled = expr.compile(dialect=postgresql.dialect())
        bound = list(compiled.params.values())[0]
        self.assertIsInstance(bound, float)

    def test_gte_leaves_non_numeric_string_as_string_for_text_columns(self):
        # A user could use gte on a text column (e.g. alert_title >= 'm').
        # Non-numeric strings must pass through unchanged.
        expr = build_condition(_STR_COL, 'gte', 'm')
        from sqlalchemy.dialects import postgresql
        compiled = expr.compile(dialect=postgresql.dialect())
        bound = list(compiled.params.values())[0]
        self.assertIsInstance(bound, str)

    def test_operator_outside_the_vocabulary_still_raises(self):
        # 'gt'/'lt' are not offered by the builder and remain unsupported —
        # adding gte/lte must not open the door to arbitrary operators.
        with self.assertRaises(ValueError):
            build_condition(_INT_COL, 'gt', 3)


class TestBuildConditionIlikeOnNonTextColumns(TestCase):
    """Regression for GlitchTip #214.

    `like` on an integer column compiled to `integer ~~* unknown`, which
    Postgres only rejects at execution time — so it escaped the try/except
    around query construction in `rule_dry_run` and surfaced as a 500.
    """

    def test_like_on_integer_column_casts_to_text(self):
        expr = build_condition(_INT_COL, 'like', 0)
        self.assertIn('CAST', str(expr).upper())

    def test_not_like_on_integer_column_casts_to_text(self):
        expr = build_condition(_INT_COL, 'not_like', 0)
        self.assertIn('CAST', str(expr).upper())

    def test_like_on_string_column_is_not_cast(self):
        # Text columns must stay uncast so existing indexes still apply.
        expr = build_condition(_STR_COL, 'like', 'foo')
        self.assertNotIn('CAST', str(expr).upper())

    def test_not_like_on_string_column_is_not_cast(self):
        expr = build_condition(_STR_COL, 'not_like', 'foo')
        self.assertNotIn('CAST', str(expr).upper())


# ---------------------------------------------------------------------------
# get_field_from_model
# ---------------------------------------------------------------------------

class TestGetFieldFromModel(TestCase):

    def _make_model(self, **attrs):
        """Minimal stub that behaves like a SQLAlchemy model class."""
        model = MagicMock()
        model.__name__ = 'FakeModel'
        for name, value in attrs.items():
            setattr(model, name, value)
        return model

    def test_existing_field_returned(self):
        col = object()
        model = self._make_model(alert_id=col)
        with patch('app.datamgmt.filtering.RESTRICTED_USER_FIELDS', set()):
            result = get_field_from_model(model, 'alert_id')
        self.assertIs(col, result)

    def test_missing_field_raises_value_error(self):
        model = MagicMock()
        model.__name__ = 'FakeModel'
        # getattr returns None for an attribute not explicitly set only if
        # spec is used; use spec to force AttributeError-like behaviour.
        model2 = MagicMock(spec=['__name__'])
        model2.__name__ = 'FakeModel'
        with self.assertRaises(ValueError):
            get_field_from_model(model2, 'nonexistent')

    def test_restricted_field_raises_value_error_when_value_matches_set_member(self):
        # RESTRICTED_USER_FIELDS is a set of strings; the check `field in RESTRICTED_USER_FIELDS`
        # works when the attribute value is itself a string that's in the set (e.g. the field
        # name string was stored as the attribute value on a stub model).
        model = self._make_model(password='password')
        with self.assertRaises(ValueError):
            get_field_from_model(model, 'password')

    def test_mfa_secrets_is_restricted_when_value_matches(self):
        model = self._make_model(mfa_secrets='mfa_secrets')
        with self.assertRaises(ValueError):
            get_field_from_model(model, 'mfa_secrets')
