#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for the per-user war-room tab order preference.

Pure helpers — no Flask context, no DB. The user is a stand-in carrying
only the `preferences` bag the helpers read and write.
"""

from types import SimpleNamespace
from unittest import TestCase

from app.business.users import users_get_war_room_tab_order
from app.business.users import users_set_war_room_tab_order
from app.models.errors import BusinessProcessingError


def _user(preferences=None):
    return SimpleNamespace(preferences=preferences)


class TestUsersGetWarRoomTabOrder(TestCase):

    def test_defaults_to_empty(self):
        self.assertEqual([], users_get_war_room_tab_order(_user()))
        self.assertEqual([], users_get_war_room_tab_order(_user({})))

    def test_returns_stored_value(self):
        order = ['chat', 'board', 'scope']
        self.assertEqual(order, users_get_war_room_tab_order(_user({'war_room_tab_order': order})))

    def test_falls_back_on_garbage_stored_verbatim(self):
        for value in ('chat', ['chat', 'chat'], ['Chat'], [1], [None], {'chat': 1}):
            self.assertEqual([], users_get_war_room_tab_order(_user({'war_room_tab_order': value})))


class TestUsersSetWarRoomTabOrder(TestCase):

    def test_stores_value_and_keeps_other_preferences(self):
        user = _user({'timezone': 'UTC'})
        users_set_war_room_tab_order(user, ['chat', 'board'])
        self.assertEqual({'timezone': 'UTC', 'war_room_tab_order': ['chat', 'board']}, user.preferences)

    def test_empty_list_clears_the_key(self):
        user = _user({'war_room_tab_order': ['chat']})
        users_set_war_room_tab_order(user, [])
        self.assertEqual({}, user.preferences)

    def test_replaces_the_dict_rather_than_mutating_it(self):
        original = {'war_room_tab_order': ['chat']}
        user = _user(original)
        users_set_war_room_tab_order(user, ['board'])
        self.assertIsNot(original, user.preferences)
        self.assertEqual({'war_room_tab_order': ['chat']}, original)

    def test_accepts_keys_with_digits_dashes_and_underscores(self):
        user = _user()
        users_set_war_room_tab_order(user, ['sit-reps', 'time_lines2'])
        self.assertEqual(['sit-reps', 'time_lines2'], user.preferences['war_room_tab_order'])

    def test_rejects_invalid_values(self):
        user = _user({'war_room_tab_order': ['chat']})
        for value in (None, 'chat', {'chat': 1}, ['chat', 'chat'], ['Chat'], ['1chat'], [''], [1],
                      ['a' * 33], [f'tab{index}' for index in range(33)]):
            with self.assertRaises(BusinessProcessingError):
                users_set_war_room_tab_order(user, value)
        self.assertEqual({'war_room_tab_order': ['chat']}, user.preferences)
