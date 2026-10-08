#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 3 of the License, or (at your option) any later version.

"""Unit tests for `mentions_added_user_ids`: a save notifies the mentions
it added, not every mention the content holds."""

from unittest import TestCase
from unittest.mock import patch

from app.iris_engine.notifications.mentions import mentions_added_user_ids


def _user(user_id):
    return f'<span data-mention data-kind="user" data-id="{user_id}" data-label="u">@u</span>'


def _team(team_id):
    return f'<span data-mention data-kind="team" data-id="{team_id}" data-label="t">@t</span>'


_MODULE = 'app.iris_engine.notifications.mentions'


class TestMentionsAddedUserIds(TestCase):

    def test_should_return_every_mention_without_previous_content(self):
        self.assertEqual({1, 2}, mentions_added_user_ids(None, f'{_user(1)} {_user(2)}'))

    def test_should_return_nothing_when_the_mentions_did_not_change(self):
        self.assertEqual(set(), mentions_added_user_ids(f'<p>{_user(1)}</p>', f'<p>{_user(1)} more text</p>'))

    def test_should_return_only_the_added_mention(self):
        self.assertEqual({2}, mentions_added_user_ids(_user(1), f'{_user(1)} {_user(2)}'))

    def test_should_return_nothing_when_a_mention_was_removed(self):
        self.assertEqual(set(), mentions_added_user_ids(f'{_user(1)} {_user(2)}', _user(1)))

    def test_should_return_nothing_for_empty_content(self):
        self.assertEqual(set(), mentions_added_user_ids(_user(1), ''))

    def test_should_not_query_when_no_mention_was_added(self):
        with patch(f'{_MODULE}.resolve_user_handles') as resolve_handles, \
                patch(f'{_MODULE}.resolve_mentions_to_user_ids') as resolve_mentions:
            added = mentions_added_user_ids('ping @alice and @bob', 'ping @alice and @bob, again', war_room_id=3)
        self.assertEqual(set(), added)
        resolve_handles.assert_not_called()
        resolve_mentions.assert_not_called()

    def test_should_resolve_handles_only_when_one_was_added(self):
        with patch(f'{_MODULE}.resolve_user_handles', side_effect=lambda handles: {
            {'alice': 1, 'bob': 2}[h] for h in handles
        }):
            self.assertEqual({2}, mentions_added_user_ids('ping @alice', 'ping @alice and @bob'))

    def test_should_expand_an_added_team_in_a_war_room(self):
        with patch(f'{_MODULE}.resolve_mentions_to_user_ids', side_effect=lambda content, _: {
            _user(1): {1},
            f'{_user(1)} {_team(7)}': {1, 5, 6},
        }[content]) as resolve:
            added = mentions_added_user_ids(_user(1), f'{_user(1)} {_team(7)}', war_room_id=3)
        self.assertEqual({5, 6}, added)
        resolve.assert_any_call(_user(1), 3)

    def test_should_ignore_team_mentions_outside_a_war_room(self):
        self.assertEqual(set(), mentions_added_user_ids(_user(1), f'{_user(1)} {_team(7)}'))
