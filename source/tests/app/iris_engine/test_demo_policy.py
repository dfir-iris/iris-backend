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

"""Unit tests for the demo-mode protection rules.

These rules are the only thing standing between a demo visitor and the
accounts the *next* visitor logs in with: every visitor of a demo
instance holds `server_administrator`, so permissions cannot express
the boundary and these predicates have to. Each branch is spelled out
one case at a time because a silent False here is not a visible bug —
the write simply succeeds — and because two of the answers are
deliberately asymmetric (the owner is exempt for groups, nobody is
exempt for seeded accounts).

The seeded-login list is duplicated in effect by `gen_demo_users` /
`gen_demo_admins` (which create the accounts) and by the demo landing
page (which publishes their passwords). All three must agree on the
`range(1, count)` bound, so it is pinned here.
"""

from unittest import TestCase

from app.iris_engine.demo_policy import DEFAULT_ADMIN_LOGIN
from app.iris_engine.demo_policy import DEMO_SEEDED_GROUP_IDS
from app.iris_engine.demo_policy import coerce_user_ids
from app.iris_engine.demo_policy import is_protected_demo_group
from app.iris_engine.demo_policy import is_protected_demo_user
from app.iris_engine.demo_policy import membership_change
from app.iris_engine.demo_policy import protected_demo_logins
from app.iris_engine.demo_policy import seeded_demo_logins

_OWNER_ID = 1
_DEFAULT_LOGINS = protected_demo_logins(10, 4)


class TestSeededDemoLogins(TestCase):

    def test_default_counts_stop_one_short_like_the_generators(self):
        self.assertEqual(['user_std_1', 'user_std_2', 'user_std_3', 'user_std_4',
                          'user_std_5', 'user_std_6', 'user_std_7', 'user_std_8',
                          'user_std_9',
                          'adm_1', 'adm_2', 'adm_3'],
                         seeded_demo_logins(10, 4))

    def test_counts_read_from_configuration_may_be_strings(self):
        self.assertEqual(seeded_demo_logins(3, 2), seeded_demo_logins('3', '2'))

    def test_count_of_one_seeds_nothing(self):
        self.assertEqual([], seeded_demo_logins(1, 1))

    def test_count_of_zero_seeds_nothing(self):
        self.assertEqual([], seeded_demo_logins(0, 0))


class TestProtectedDemoLogins(TestCase):

    def test_adds_the_default_administrator_to_the_seeded_accounts(self):
        self.assertEqual(seeded_demo_logins(10, 4) + [DEFAULT_ADMIN_LOGIN],
                         protected_demo_logins(10, 4))

    def test_uses_the_configured_administrator_login_when_there_is_one(self):
        self.assertIn('root_adm', protected_demo_logins(10, 4, 'root_adm'))
        self.assertNotIn(DEFAULT_ADMIN_LOGIN, protected_demo_logins(10, 4, 'root_adm'))

    def test_falls_back_to_the_default_when_the_configured_login_is_empty(self):
        # `IRIS_ADM_USERNAME` is absent from most configurations, so
        # `app.config.get` hands down None — and an empty string is
        # what an env var set to nothing yields.
        self.assertIn(DEFAULT_ADMIN_LOGIN, protected_demo_logins(10, 4, None))
        self.assertIn(DEFAULT_ADMIN_LOGIN, protected_demo_logins(10, 4, ''))

    def test_administrator_is_protected_even_with_no_seeded_accounts(self):
        self.assertEqual([DEFAULT_ADMIN_LOGIN], protected_demo_logins(1, 1))


class TestIsProtectedDemoUser(TestCase):

    def test_seeded_standard_account_is_protected(self):
        self.assertTrue(is_protected_demo_user(42, 'user_std_3', 7, _OWNER_ID, _DEFAULT_LOGINS))

    def test_seeded_admin_account_is_protected(self):
        self.assertTrue(is_protected_demo_user(42, 'adm_2', 7, _OWNER_ID, _DEFAULT_LOGINS))

    def test_seeded_account_is_protected_from_the_owner_too(self):
        # The password is published on the landing page and shared by
        # every visitor — there is nobody for whom changing it is safe.
        self.assertTrue(is_protected_demo_user(42, 'adm_2', _OWNER_ID, _OWNER_ID, _DEFAULT_LOGINS))

    def test_account_created_during_a_demo_session_is_not_protected(self):
        self.assertFalse(is_protected_demo_user(42, 'analyst', 7, _OWNER_ID, _DEFAULT_LOGINS))

    def test_login_just_past_the_seeded_range_is_not_protected(self):
        self.assertFalse(is_protected_demo_user(42, 'user_std_10', 7, _OWNER_ID, _DEFAULT_LOGINS))

    def test_administrator_is_protected_from_a_visitor(self):
        self.assertTrue(is_protected_demo_user(_OWNER_ID, DEFAULT_ADMIN_LOGIN, 7, _OWNER_ID,
                                               _DEFAULT_LOGINS))

    def test_administrator_is_protected_from_itself(self):
        # The account that owns the instance is frozen for everyone in
        # demo mode: its password is as good as public and losing it
        # takes the instance with it.
        self.assertTrue(is_protected_demo_user(_OWNER_ID, DEFAULT_ADMIN_LOGIN, _OWNER_ID,
                                               _OWNER_ID, _DEFAULT_LOGINS))

    def test_administrator_is_protected_whatever_its_id(self):
        # Nothing guarantees the admin account is id 1 — a deployment
        # that seeded users first gets a higher id.
        self.assertTrue(is_protected_demo_user(57, DEFAULT_ADMIN_LOGIN, 7, _OWNER_ID,
                                               _DEFAULT_LOGINS))

    def test_configured_admin_login_is_protected_in_place_of_the_default(self):
        logins = protected_demo_logins(10, 4, 'root_adm')
        self.assertTrue(is_protected_demo_user(57, 'root_adm', 7, _OWNER_ID, logins))
        self.assertFalse(is_protected_demo_user(57, DEFAULT_ADMIN_LOGIN, 7, _OWNER_ID, logins))

    def test_owner_id_is_protected_from_a_non_owner_caller_whatever_its_login(self):
        # Second, independent reason: whoever holds
        # `DEMO_MODE_OWNER_USER_ID` is protected from other visitors
        # even if the admin account was renamed.
        self.assertTrue(is_protected_demo_user(_OWNER_ID, 'renamed', 7, _OWNER_ID, []))

    def test_owner_may_edit_their_own_renamed_account(self):
        self.assertFalse(is_protected_demo_user(_OWNER_ID, 'renamed', _OWNER_ID, _OWNER_ID, []))

    def test_unknown_caller_is_treated_as_not_the_owner(self):
        # `caller_id` is None outside a request context — the
        # serialisers ask this question to flag rows for the UI.
        self.assertTrue(is_protected_demo_user(_OWNER_ID, 'renamed', None, _OWNER_ID, []))

    def test_no_protected_logins_leaves_ordinary_accounts_open(self):
        self.assertFalse(is_protected_demo_user(42, 'user_std_3', 7, _OWNER_ID, []))


class TestIsProtectedDemoGroup(TestCase):

    def test_seeded_groups_are_protected_from_a_visitor(self):
        for group_id in DEMO_SEEDED_GROUP_IDS:
            self.assertTrue(is_protected_demo_group(group_id, 7, _OWNER_ID))

    def test_group_created_during_a_demo_session_is_not_protected(self):
        self.assertFalse(is_protected_demo_group(3, 7, _OWNER_ID))

    def test_owner_may_edit_the_seeded_groups(self):
        for group_id in DEMO_SEEDED_GROUP_IDS:
            self.assertFalse(is_protected_demo_group(group_id, _OWNER_ID, _OWNER_ID))

    def test_unknown_caller_is_treated_as_not_the_owner(self):
        self.assertTrue(is_protected_demo_group(1, None, _OWNER_ID))


class TestCoerceUserIds(TestCase):

    def test_numeric_strings_become_ints(self):
        self.assertEqual({1, 2}, coerce_user_ids(['1', 2]))

    def test_duplicates_collapse(self):
        self.assertEqual({1}, coerce_user_ids([1, '1', 1]))

    def test_non_numeric_entries_are_skipped_rather_than_raised_on(self):
        self.assertEqual({3}, coerce_user_ids(['abc', None, {}, 3]))

    def test_empty_input_yields_empty_set(self):
        self.assertEqual(set(), coerce_user_ids([]))


class TestMembershipChange(TestCase):

    def test_replaying_the_current_members_changes_nothing(self):
        self.assertEqual(set(), membership_change({1, 2, 3}, [1, 2, 3]))

    def test_reordering_the_current_members_changes_nothing(self):
        self.assertEqual(set(), membership_change({1, 2, 3}, [3, 1, 2]))

    def test_added_member_is_reported(self):
        self.assertEqual({4}, membership_change({1, 2}, [1, 2, 4]))

    def test_removed_member_is_reported(self):
        self.assertEqual({2}, membership_change({1, 2}, [1]))

    def test_added_and_removed_members_are_both_reported(self):
        self.assertEqual({2, 4}, membership_change({1, 2}, [1, 4]))

    def test_emptying_the_group_reports_every_current_member(self):
        self.assertEqual({1, 2}, membership_change({1, 2}, []))

    def test_string_ids_do_not_look_like_a_change(self):
        # The v1 route takes the submitted list straight from JSON, so a
        # client sending ids as strings must not make an unchanged
        # membership look like a full swap.
        self.assertEqual(set(), membership_change({1, 2}, ['1', '2']))
