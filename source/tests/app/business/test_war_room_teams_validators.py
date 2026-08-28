#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for the pure validation helpers in business/war_room_teams.py."""

from unittest import TestCase

from app.business.war_room_teams import _validate_color, _validate_name
from app.models.errors import BusinessProcessingError


class TestTeamValidateName(TestCase):

    def test_valid_name_returned_stripped(self):
        self.assertEqual('Alpha Team', _validate_name('  Alpha Team  '))

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
        result = _validate_name('a' * 80)
        self.assertEqual(80, len(result))

    def test_name_over_limit_raises(self):
        with self.assertRaises(BusinessProcessingError):
            _validate_name('a' * 81)


class TestTeamValidateColor(TestCase):

    def test_none_returns_none(self):
        self.assertIsNone(_validate_color(None))

    def test_empty_string_returns_none(self):
        self.assertIsNone(_validate_color(''))

    def test_valid_hex_accepted(self):
        self.assertEqual('#123abc', _validate_color('#123abc'))

    def test_uppercase_hex_accepted(self):
        self.assertEqual('#ABCDEF', _validate_color('#ABCDEF'))

    def test_invalid_color_raises(self):
        with self.assertRaises(BusinessProcessingError):
            _validate_color('green')

    def test_non_string_raises(self):
        with self.assertRaises(BusinessProcessingError):
            _validate_color(0xFFFFFF)
