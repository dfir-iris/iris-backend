#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for the scope-checking logic in business/access_controls.py.

`access_controls_user_has_customer_scope` is tested by patching the two
functions it delegates to — no DB needed.
"""

from unittest import TestCase
from unittest.mock import MagicMock, patch


class TestAccessControlsUserHasCustomerScope(TestCase):

    def _call(self, customer_scope, is_admin=False, has_access_for=None):
        """Call with patched inner helpers.

        `has_access_for` is a set of customer_ids the user CAN access.
        """
        from app.business.access_controls import access_controls_user_has_customer_scope

        user = MagicMock()
        permissions = MagicMock()

        if has_access_for is None:
            has_access_for = set()

        with patch(
            'app.business.access_controls.ac_has_permission_server_administrator',
            return_value=is_admin,
        ), patch(
            'app.business.access_controls.access_controls_user_has_customer_access',
            side_effect=lambda _u, _p, cid, **_kw: cid in has_access_for,
        ):
            return access_controls_user_has_customer_scope(
                user, permissions, customer_scope
            )

    def test_admin_always_true_regardless_of_scope(self):
        self.assertTrue(self._call(customer_scope=None, is_admin=True))

    def test_admin_true_for_empty_list(self):
        self.assertTrue(self._call(customer_scope=[], is_admin=True))

    def test_admin_true_for_non_empty_list(self):
        self.assertTrue(self._call(customer_scope=[1, 2], is_admin=True))

    def test_non_admin_none_scope_returns_false(self):
        self.assertFalse(self._call(customer_scope=None, is_admin=False))

    def test_non_admin_empty_list_returns_false(self):
        self.assertFalse(self._call(customer_scope=[], is_admin=False))

    def test_non_admin_unknown_type_returns_false(self):
        self.assertFalse(self._call(customer_scope='global', is_admin=False))

    def test_non_admin_has_access_to_all_customers_returns_true(self):
        result = self._call(customer_scope=[1, 2], is_admin=False, has_access_for={1, 2})
        self.assertTrue(result)

    def test_non_admin_lacks_access_to_one_customer_returns_false(self):
        result = self._call(customer_scope=[1, 2], is_admin=False, has_access_for={1})
        self.assertFalse(result)

    def test_non_admin_has_no_access_returns_false(self):
        result = self._call(customer_scope=[1], is_admin=False, has_access_for=set())
        self.assertFalse(result)

    def test_single_customer_user_has_access_returns_true(self):
        result = self._call(customer_scope=[5], is_admin=False, has_access_for={5})
        self.assertTrue(result)

    def test_tuple_scope_accepted(self):
        result = self._call(customer_scope=(1,), is_admin=False, has_access_for={1})
        self.assertTrue(result)
