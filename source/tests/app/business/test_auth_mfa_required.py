#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for _mfa_required_for_user in business/auth.py.

mfa_is_enforced() touches app.config, so we patch it at the module level.
No Flask context, no DB needed.
"""

from unittest import TestCase
from unittest.mock import MagicMock, patch

from app.business.auth import _mfa_required_for_user


def _user(mfa_setup_complete=True, mfa_secrets='TOTP_SECRET'):
    user = MagicMock()
    user.mfa_setup_complete = mfa_setup_complete
    user.mfa_secrets = mfa_secrets
    return user


class TestMfaRequiredForUser(TestCase):

    def test_enforced_and_user_has_mfa_returns_true(self):
        with patch('app.business.auth.mfa_is_enforced', return_value=True):
            self.assertTrue(_mfa_required_for_user(_user()))

    def test_enforced_but_no_mfa_setup_still_returns_true(self):
        # VI-003: an unenrolled account must get a step-1 token under an
        # MFA-enforced policy, not a fully-verified one. Returning False
        # here is precisely the enrollment bypass.
        with patch('app.business.auth.mfa_is_enforced', return_value=True):
            self.assertTrue(_mfa_required_for_user(_user(mfa_setup_complete=False)))

    def test_enforced_but_no_mfa_secrets_still_returns_true(self):
        with patch('app.business.auth.mfa_is_enforced', return_value=True):
            self.assertTrue(_mfa_required_for_user(_user(mfa_secrets=None)))

    def test_not_enforced_even_with_mfa_setup_returns_false(self):
        with patch('app.business.auth.mfa_is_enforced', return_value=False):
            self.assertFalse(_mfa_required_for_user(_user()))

    def test_not_enforced_no_mfa_returns_false(self):
        with patch('app.business.auth.mfa_is_enforced', return_value=False):
            self.assertFalse(_mfa_required_for_user(_user(mfa_setup_complete=False, mfa_secrets=None)))

    def test_user_without_mfa_attributes_follows_the_server_policy(self):
        user = MagicMock(spec=[])
        with patch('app.business.auth.mfa_is_enforced', return_value=True):
            self.assertTrue(_mfa_required_for_user(user))
        with patch('app.business.auth.mfa_is_enforced', return_value=False):
            self.assertFalse(_mfa_required_for_user(user))
