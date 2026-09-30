#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for the per-user clock (12h/24h) preference.

Pure helpers — no Flask context, no DB. The user is a stand-in carrying
only the `preferences` bag the helpers read and write.
"""

from types import SimpleNamespace
from unittest import TestCase

from app.business.users import USERS_TIME_FORMAT_DEFAULT
from app.business.users import users_get_time_format
from app.business.users import users_set_time_format
from app.models.errors import BusinessProcessingError


def _user(preferences=None):
    return SimpleNamespace(preferences=preferences)


class TestUsersGetTimeFormat(TestCase):

    def test_defaults_to_24h(self):
        self.assertEqual('24h', USERS_TIME_FORMAT_DEFAULT)
        self.assertEqual(USERS_TIME_FORMAT_DEFAULT, users_get_time_format(_user()))
        self.assertEqual(USERS_TIME_FORMAT_DEFAULT, users_get_time_format(_user({})))

    def test_returns_stored_value(self):
        self.assertEqual('12h', users_get_time_format(_user({'time_format': '12h'})))
        self.assertEqual('locale', users_get_time_format(_user({'time_format': 'locale'})))

    def test_falls_back_on_garbage_stored_verbatim(self):
        self.assertEqual(USERS_TIME_FORMAT_DEFAULT, users_get_time_format(_user({'time_format': '13h'})))
        self.assertEqual(USERS_TIME_FORMAT_DEFAULT, users_get_time_format(_user({'time_format': ['12h']})))


class TestUsersSetTimeFormat(TestCase):

    def test_stores_value_and_keeps_other_preferences(self):
        user = _user({'timezone': 'UTC'})
        users_set_time_format(user, '12h')
        self.assertEqual({'timezone': 'UTC', 'time_format': '12h'}, user.preferences)

    def test_default_clears_the_key(self):
        user = _user({'time_format': '12h'})
        users_set_time_format(user, USERS_TIME_FORMAT_DEFAULT)
        self.assertEqual({}, user.preferences)

    def test_replaces_the_dict_rather_than_mutating_it(self):
        original = {'time_format': '12h'}
        user = _user(original)
        users_set_time_format(user, 'locale')
        self.assertIsNot(original, user.preferences)
        self.assertEqual({'time_format': '12h'}, original)

    def test_rejects_unknown_values(self):
        user = _user({'time_format': '12h'})
        for value in ('13h', '', None, 12, ['12h']):
            with self.assertRaises(BusinessProcessingError):
                users_set_time_format(user, value)
        self.assertEqual({'time_format': '12h'}, user.preferences)
