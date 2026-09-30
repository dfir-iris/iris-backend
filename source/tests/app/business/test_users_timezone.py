#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for the per-user display timezone preference.

Pure helpers — no Flask context, no DB. The user is a stand-in carrying
only the `preferences` bag the helpers read and write.
"""

from types import SimpleNamespace
from unittest import TestCase

from app.business.users import USERS_TIMEZONE_BROWSER
from app.business.users import users_get_timezone
from app.business.users import users_is_valid_timezone
from app.business.users import users_set_timezone
from app.models.errors import BusinessProcessingError


def _user(preferences=None):
    return SimpleNamespace(preferences=preferences)


class TestUsersIsValidTimezone(TestCase):

    def test_browser_is_valid(self):
        self.assertTrue(users_is_valid_timezone(USERS_TIMEZONE_BROWSER))

    def test_iana_names_are_valid(self):
        self.assertTrue(users_is_valid_timezone('UTC'))
        self.assertTrue(users_is_valid_timezone('Europe/Paris'))
        self.assertTrue(users_is_valid_timezone('America/Argentina/Buenos_Aires'))

    def test_unknown_name_is_invalid(self):
        self.assertFalse(users_is_valid_timezone('Mars/Olympus_Mons'))

    def test_path_traversal_is_invalid(self):
        self.assertFalse(users_is_valid_timezone('../../etc/passwd'))
        self.assertFalse(users_is_valid_timezone('/etc/localtime'))

    def test_non_strings_are_invalid(self):
        self.assertFalse(users_is_valid_timezone(None))
        self.assertFalse(users_is_valid_timezone(''))
        self.assertFalse(users_is_valid_timezone(2))
        self.assertFalse(users_is_valid_timezone(['UTC']))

    def test_overlong_name_is_invalid(self):
        self.assertFalse(users_is_valid_timezone('A' * 65))


class TestUsersGetTimezone(TestCase):

    def test_defaults_to_browser_without_preferences(self):
        self.assertEqual(USERS_TIMEZONE_BROWSER, users_get_timezone(_user()))
        self.assertEqual(USERS_TIMEZONE_BROWSER, users_get_timezone(_user({})))

    def test_returns_stored_zone(self):
        self.assertEqual('Europe/Paris', users_get_timezone(_user({'timezone': 'Europe/Paris'})))

    def test_falls_back_to_browser_on_invalid_stored_value(self):
        # The generic preferences route stores values verbatim.
        self.assertEqual(USERS_TIMEZONE_BROWSER, users_get_timezone(_user({'timezone': 'Nope/Nope'})))
        self.assertEqual(USERS_TIMEZONE_BROWSER, users_get_timezone(_user({'timezone': {'a': 1}})))


class TestUsersSetTimezone(TestCase):

    def test_stores_zone_and_keeps_other_preferences(self):
        user = _user({'war_room_stream': ['chat']})
        users_set_timezone(user, 'UTC')
        self.assertEqual({'war_room_stream': ['chat'], 'timezone': 'UTC'}, user.preferences)

    def test_replaces_the_preferences_dict(self):
        preferences = {}
        user = _user(preferences)
        users_set_timezone(user, 'UTC')
        self.assertIsNot(preferences, user.preferences)

    def test_browser_clears_the_stored_zone(self):
        user = _user({'timezone': 'UTC', 'other': 1})
        users_set_timezone(user, USERS_TIMEZONE_BROWSER)
        self.assertEqual({'other': 1}, user.preferences)

    def test_invalid_zone_raises_and_leaves_preferences_untouched(self):
        user = _user({'timezone': 'UTC'})
        with self.assertRaises(BusinessProcessingError):
            users_set_timezone(user, 'Nope/Nope')
        self.assertEqual({'timezone': 'UTC'}, user.preferences)
