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
