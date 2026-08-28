#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for pure access-control helper functions.

These functions deal with bitmask arithmetic and list conversion —
they are deterministic, pure Python, and require no DB or request
context.  The auth layer is safety-critical so every edge case (zero
mask, full mask, identity of analyst mask, multi-group deduplication)
is spelled out individually.
"""

from unittest import TestCase

from app.iris_engine.access_control.utils import (
    ac_combine_groups_access,
    ac_get_mask_analyst,
    ac_get_mask_full_permissions,
    ac_mask_from_val_list,
    ac_permission_to_list,
)
from app.models.authorization import (
    CaseAccessLevel,
    Permissions,
    ac_access_level_mask_from_val_list,
    ac_flag_match_mask,
    ac_has_permission_server_administrator,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _Group:
    """Minimal stand-in for the ORM Group/UserGroup join row."""
    def __init__(self, user_id, access_level):
        self.id = user_id
        self.group_auto_follow_access_level = access_level


# ---------------------------------------------------------------------------
# ac_flag_match_mask
# ---------------------------------------------------------------------------

class TestAcFlagMatchMask(TestCase):

    def test_exact_match_returns_true(self):
        self.assertTrue(ac_flag_match_mask(0b111, 0b111))

    def test_superset_returns_true(self):
        self.assertTrue(ac_flag_match_mask(0b1111, 0b0011))

    def test_subset_returns_false(self):
        self.assertFalse(ac_flag_match_mask(0b0001, 0b0011))

    def test_disjoint_bits_returns_false(self):
        self.assertFalse(ac_flag_match_mask(0b1100, 0b0011))

    def test_zero_mask_always_matches(self):
        self.assertTrue(ac_flag_match_mask(0, 0))
        self.assertTrue(ac_flag_match_mask(0xFF, 0))

    def test_zero_flag_with_nonzero_mask_does_not_match(self):
        self.assertFalse(ac_flag_match_mask(0, 1))


# ---------------------------------------------------------------------------
# ac_has_permission_server_administrator
# ---------------------------------------------------------------------------

class TestHasPermissionServerAdministrator(TestCase):

    def test_administrator_bit_set(self):
        mask = Permissions.server_administrator.value
        self.assertTrue(ac_has_permission_server_administrator(mask))

    def test_administrator_bit_plus_others(self):
        mask = Permissions.server_administrator.value | Permissions.standard_user.value
        self.assertTrue(ac_has_permission_server_administrator(mask))

    def test_standard_user_only_is_not_administrator(self):
        mask = Permissions.standard_user.value
        self.assertFalse(ac_has_permission_server_administrator(mask))

    def test_zero_mask_is_not_administrator(self):
        self.assertFalse(ac_has_permission_server_administrator(0))

    def test_full_mask_is_administrator(self):
        mask = ac_get_mask_full_permissions()
        self.assertTrue(ac_has_permission_server_administrator(mask))


# ---------------------------------------------------------------------------
# ac_get_mask_full_permissions
# ---------------------------------------------------------------------------

class TestAcGetMaskFullPermissions(TestCase):

    def test_full_mask_covers_every_permission(self):
        full = ac_get_mask_full_permissions()
        for perm in Permissions:
            self.assertTrue(
                ac_flag_match_mask(full, perm.value),
                f'{perm.name} not in full mask'
            )

    def test_full_mask_is_positive(self):
        self.assertGreater(ac_get_mask_full_permissions(), 0)

    def test_full_mask_is_deterministic(self):
        self.assertEqual(ac_get_mask_full_permissions(), ac_get_mask_full_permissions())


# ---------------------------------------------------------------------------
# ac_get_mask_analyst
# ---------------------------------------------------------------------------

class TestAcGetMaskAnalyst(TestCase):

    def test_analyst_mask_includes_standard_user(self):
        mask = ac_get_mask_analyst()
        self.assertTrue(ac_flag_match_mask(mask, Permissions.standard_user.value))

    def test_analyst_mask_includes_alerts_read(self):
        mask = ac_get_mask_analyst()
        self.assertTrue(ac_flag_match_mask(mask, Permissions.alerts_read.value))

    def test_analyst_mask_includes_alerts_write(self):
        mask = ac_get_mask_analyst()
        self.assertTrue(ac_flag_match_mask(mask, Permissions.alerts_write.value))

    def test_analyst_mask_includes_search_across_cases(self):
        mask = ac_get_mask_analyst()
        self.assertTrue(ac_flag_match_mask(mask, Permissions.search_across_cases.value))

    def test_analyst_mask_includes_customers_read(self):
        mask = ac_get_mask_analyst()
        self.assertTrue(ac_flag_match_mask(mask, Permissions.customers_read.value))

    def test_analyst_mask_includes_activities_read(self):
        mask = ac_get_mask_analyst()
        self.assertTrue(ac_flag_match_mask(mask, Permissions.activities_read.value))

    def test_analyst_mask_does_not_include_server_administrator(self):
        mask = ac_get_mask_analyst()
        self.assertFalse(ac_flag_match_mask(mask, Permissions.server_administrator.value))

    def test_analyst_mask_is_subset_of_full_mask(self):
        analyst = ac_get_mask_analyst()
        full = ac_get_mask_full_permissions()
        self.assertEqual(analyst, analyst & full)


# ---------------------------------------------------------------------------
# ac_permission_to_list
# ---------------------------------------------------------------------------

class TestAcPermissionToList(TestCase):

    def test_zero_mask_returns_empty_list(self):
        self.assertEqual([], ac_permission_to_list(0))

    def test_single_permission_returned(self):
        result = ac_permission_to_list(Permissions.standard_user.value)
        self.assertEqual(1, len(result))
        self.assertEqual('standard_user', result[0]['name'])
        self.assertEqual(Permissions.standard_user.value, result[0]['value'])

    def test_two_permissions_returned(self):
        mask = Permissions.standard_user.value | Permissions.alerts_read.value
        result = ac_permission_to_list(mask)
        names = {r['name'] for r in result}
        self.assertIn('standard_user', names)
        self.assertIn('alerts_read', names)

    def test_full_mask_returns_all_permissions(self):
        full = ac_get_mask_full_permissions()
        result = ac_permission_to_list(full)
        self.assertEqual(len(Permissions), len(result))

    def test_result_entries_have_name_and_value_keys(self):
        result = ac_permission_to_list(Permissions.server_administrator.value)
        self.assertIn('name', result[0])
        self.assertIn('value', result[0])


# ---------------------------------------------------------------------------
# ac_mask_from_val_list
# ---------------------------------------------------------------------------

class TestAcMaskFromValList(TestCase):

    def test_empty_list_returns_zero(self):
        self.assertEqual(0, ac_mask_from_val_list([]))

    def test_single_value_returned(self):
        self.assertEqual(1, ac_mask_from_val_list([1]))

    def test_combines_two_values(self):
        self.assertEqual(3, ac_mask_from_val_list([1, 2]))

    def test_string_values_coerced(self):
        # The function uses int() so strings work
        self.assertEqual(3, ac_mask_from_val_list(['1', '2']))

    def test_idempotent_on_duplicates(self):
        # OR-ing the same bit twice is still just that bit
        self.assertEqual(1, ac_mask_from_val_list([1, 1]))

    def test_all_permissions_combined(self):
        values = [p.value for p in Permissions]
        self.assertEqual(ac_get_mask_full_permissions(), ac_mask_from_val_list(values))


# ---------------------------------------------------------------------------
# ac_combine_groups_access
# ---------------------------------------------------------------------------

class TestAcCombineGroupsAccess(TestCase):

    def test_empty_list_returns_empty_dict(self):
        self.assertEqual({}, ac_combine_groups_access([]))

    def test_single_group_single_user(self):
        result = ac_combine_groups_access([_Group(user_id=1, access_level=2)])
        self.assertEqual({1: 2}, result)

    def test_highest_level_wins_for_same_user(self):
        groups = [
            _Group(user_id=1, access_level=2),
            _Group(user_id=1, access_level=4),
            _Group(user_id=1, access_level=3),
        ]
        result = ac_combine_groups_access(groups)
        self.assertEqual({1: 4}, result)

    def test_different_users_stored_separately(self):
        groups = [
            _Group(user_id=1, access_level=2),
            _Group(user_id=2, access_level=3),
        ]
        result = ac_combine_groups_access(groups)
        self.assertEqual({1: 2, 2: 3}, result)

    def test_lower_level_does_not_overwrite(self):
        groups = [
            _Group(user_id=5, access_level=10),
            _Group(user_id=5, access_level=1),
        ]
        result = ac_combine_groups_access(groups)
        self.assertEqual({5: 10}, result)


# ---------------------------------------------------------------------------
# ac_access_level_mask_from_val_list (on the model)
# ---------------------------------------------------------------------------

class TestAcAccessLevelMaskFromValList(TestCase):

    def test_empty_list_returns_zero(self):
        self.assertEqual(0, ac_access_level_mask_from_val_list([]))

    def test_single_level(self):
        level = CaseAccessLevel.read_only.value
        self.assertEqual(level, ac_access_level_mask_from_val_list([level]))

    def test_combines_two_levels(self):
        r = CaseAccessLevel.read_only.value
        w = CaseAccessLevel.full_access.value
        result = ac_access_level_mask_from_val_list([r, w])
        self.assertEqual(r | w, result)

    def test_string_values_coerced(self):
        level = CaseAccessLevel.read_only.value
        self.assertEqual(level, ac_access_level_mask_from_val_list([str(level)]))
