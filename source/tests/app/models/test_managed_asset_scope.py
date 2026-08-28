#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for the pure value object ManagedAssetViewerScope.

This class has no DB access, no Flask context and no side-effects, so every
method can be exercised with plain Python.

Key semantics under test (from the module docstring):
  * `None`  means "unrestricted" for either dimension.
  * Empty collection means "nothing visible" (deny).
  * `has_customer_access` / `has_case_access` must NOT be replaced with
    truthiness checks — None and [] have opposite meanings.
"""

from unittest import TestCase

from app.models.managed_asset_scope import ManagedAssetViewerScope


class TestManagedAssetViewerScopeDefaults(TestCase):
    """Construction with default arguments."""

    def test_defaults_client_ids_none(self):
        scope = ManagedAssetViewerScope()
        self.assertIsNone(scope.get_client_ids())

    def test_defaults_case_ids_none(self):
        scope = ManagedAssetViewerScope()
        self.assertIsNone(scope.get_case_ids())

    def test_defaults_not_administrator(self):
        scope = ManagedAssetViewerScope()
        self.assertFalse(scope.is_administrator())

    def test_defaults_is_restricted(self):
        # Non-administrator is always restricted.
        scope = ManagedAssetViewerScope()
        self.assertTrue(scope.is_restricted())

    def test_defaults_has_customer_access_none_means_all(self):
        scope = ManagedAssetViewerScope()
        self.assertTrue(scope.has_customer_access())

    def test_defaults_has_case_access_none_means_all(self):
        scope = ManagedAssetViewerScope()
        self.assertTrue(scope.has_case_access())


class TestManagedAssetViewerScopeAdministrator(TestCase):
    """Administrator flag controls is_administrator and is_restricted."""

    def test_administrator_flag_stored(self):
        scope = ManagedAssetViewerScope(is_administrator=True)
        self.assertTrue(scope.is_administrator())

    def test_administrator_not_restricted(self):
        scope = ManagedAssetViewerScope(is_administrator=True)
        self.assertFalse(scope.is_restricted())

    def test_non_administrator_is_restricted(self):
        scope = ManagedAssetViewerScope(is_administrator=False)
        self.assertTrue(scope.is_restricted())

    def test_administrator_still_sees_all_when_ids_none(self):
        scope = ManagedAssetViewerScope(is_administrator=True)
        self.assertTrue(scope.has_customer_access())
        self.assertTrue(scope.has_case_access())


class TestHasCustomerAccess(TestCase):
    """None means all; empty list means none; non-empty list means some."""

    def test_none_client_ids_grants_access(self):
        scope = ManagedAssetViewerScope(client_ids=None)
        self.assertTrue(scope.has_customer_access())

    def test_empty_client_ids_denies_access(self):
        scope = ManagedAssetViewerScope(client_ids=[])
        self.assertFalse(scope.has_customer_access())

    def test_populated_client_ids_grants_access(self):
        scope = ManagedAssetViewerScope(client_ids=[1, 2, 3])
        self.assertTrue(scope.has_customer_access())

    def test_single_client_id_grants_access(self):
        scope = ManagedAssetViewerScope(client_ids=[42])
        self.assertTrue(scope.has_customer_access())

    def test_get_client_ids_returns_exact_list(self):
        ids = [10, 20]
        scope = ManagedAssetViewerScope(client_ids=ids)
        self.assertEqual(ids, scope.get_client_ids())


class TestHasCaseAccess(TestCase):
    """None means all; empty list means none; non-empty list means some."""

    def test_none_case_ids_grants_access(self):
        scope = ManagedAssetViewerScope(case_ids=None)
        self.assertTrue(scope.has_case_access())

    def test_empty_case_ids_denies_access(self):
        scope = ManagedAssetViewerScope(case_ids=[])
        self.assertFalse(scope.has_case_access())

    def test_populated_case_ids_grants_access(self):
        scope = ManagedAssetViewerScope(case_ids=[5, 6])
        self.assertTrue(scope.has_case_access())

    def test_single_case_id_grants_access(self):
        scope = ManagedAssetViewerScope(case_ids=[99])
        self.assertTrue(scope.has_case_access())

    def test_get_case_ids_returns_exact_list(self):
        ids = [7, 8, 9]
        scope = ManagedAssetViewerScope(case_ids=ids)
        self.assertEqual(ids, scope.get_case_ids())


class TestManagedAssetViewerScopeCombinations(TestCase):
    """Realistic combined configurations."""

    def test_restricted_user_with_some_cases(self):
        scope = ManagedAssetViewerScope(
            client_ids=[1],
            case_ids=[10, 20],
            is_administrator=False,
        )
        self.assertTrue(scope.is_restricted())
        self.assertTrue(scope.has_customer_access())
        self.assertTrue(scope.has_case_access())

    def test_restricted_user_with_no_cases(self):
        scope = ManagedAssetViewerScope(
            client_ids=[1],
            case_ids=[],
            is_administrator=False,
        )
        self.assertTrue(scope.has_customer_access())
        self.assertFalse(scope.has_case_access())

    def test_restricted_user_with_no_customers(self):
        scope = ManagedAssetViewerScope(
            client_ids=[],
            case_ids=[10],
            is_administrator=False,
        )
        self.assertFalse(scope.has_customer_access())
        self.assertTrue(scope.has_case_access())

    def test_fully_locked_out_user(self):
        scope = ManagedAssetViewerScope(client_ids=[], case_ids=[])
        self.assertFalse(scope.has_customer_access())
        self.assertFalse(scope.has_case_access())

    def test_administrator_with_explicit_ids_still_sees_all(self):
        # Administrator flag overrides the restriction indicator but the id
        # lists are still stored and returned as-is.
        scope = ManagedAssetViewerScope(
            client_ids=[1],
            case_ids=[2],
            is_administrator=True,
        )
        self.assertFalse(scope.is_restricted())
        self.assertEqual([1], scope.get_client_ids())
        self.assertEqual([2], scope.get_case_ids())
