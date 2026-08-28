#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for the pure validation helpers in business/war_rooms.py.

No DB, no Flask context needed.
"""

from unittest import TestCase

from app.business.war_rooms import (
    _validate_color,
    _validate_name,
    _validate_role,
    _validate_state,
)
from app.models.errors import BusinessProcessingError


class TestValidateName(TestCase):

    def test_valid_name_returned_stripped(self):
        self.assertEqual('My War Room', _validate_name('  My War Room  '))

    def test_non_string_raises(self):
        with self.assertRaises(BusinessProcessingError):
            _validate_name(42)

    def test_empty_string_raises(self):
        with self.assertRaises(BusinessProcessingError):
            _validate_name('')

    def test_whitespace_only_raises(self):
        with self.assertRaises(BusinessProcessingError):
            _validate_name('   ')

    def test_name_at_limit_accepted(self):
        result = _validate_name('a' * 256)
        self.assertEqual(256, len(result))

    def test_name_over_limit_raises(self):
        with self.assertRaises(BusinessProcessingError):
            _validate_name('a' * 257)

    def test_valid_name_preserved(self):
        self.assertEqual('Alpha Squad', _validate_name('Alpha Squad'))


class TestValidateColor(TestCase):

    def test_none_returns_none(self):
        self.assertIsNone(_validate_color(None))

    def test_empty_string_returns_none(self):
        self.assertIsNone(_validate_color(''))

    def test_valid_hex_color(self):
        self.assertEqual('#ff0000', _validate_color('#ff0000'))

    def test_uppercase_hex_accepted(self):
        self.assertEqual('#FF0000', _validate_color('#FF0000'))

    def test_mixed_case_hex_accepted(self):
        self.assertEqual('#aAbBcC', _validate_color('#aAbBcC'))

    def test_invalid_color_raises(self):
        with self.assertRaises(BusinessProcessingError):
            _validate_color('red')

    def test_too_short_raises(self):
        with self.assertRaises(BusinessProcessingError):
            _validate_color('#abc')

    def test_too_long_raises(self):
        with self.assertRaises(BusinessProcessingError):
            _validate_color('#aabbccdd')

    def test_non_string_raises(self):
        with self.assertRaises(BusinessProcessingError):
            _validate_color(12345)


class TestValidateState(TestCase):

    def test_none_returns_none(self):
        self.assertIsNone(_validate_state(None))

    def test_open_state_accepted(self):
        self.assertEqual('open', _validate_state('open'))

    def test_active_state_accepted(self):
        self.assertEqual('active', _validate_state('active'))

    def test_standby_state_accepted(self):
        self.assertEqual('standby', _validate_state('standby'))

    def test_closed_state_accepted(self):
        self.assertEqual('closed', _validate_state('closed'))

    def test_invalid_state_raises(self):
        with self.assertRaises(BusinessProcessingError):
            _validate_state('unknown')

    def test_non_string_raises(self):
        with self.assertRaises(BusinessProcessingError):
            _validate_state(1)


class TestValidateRole(TestCase):

    def test_none_returns_default_responder(self):
        result = _validate_role(None)
        self.assertEqual('responder', result)

    def test_valid_lead_role(self):
        self.assertEqual('lead', _validate_role('lead'))

    def test_valid_responder_role(self):
        self.assertEqual('responder', _validate_role('responder'))

    def test_invalid_role_raises(self):
        with self.assertRaises(BusinessProcessingError):
            _validate_role('admin')

    def test_non_string_raises(self):
        with self.assertRaises(BusinessProcessingError):
            _validate_role(0)
