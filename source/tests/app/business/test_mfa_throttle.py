#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for the MFA-verify throttle.

A TOTP is six digits. The throttle is the only thing standing between an
attacker holding a refresh token and a million guesses, so the ceiling
and the reset-on-success behaviour are load-bearing rather than
cosmetic.

These had no coverage at all while the counter was a private dict inside
`blueprints/rest/v2/auth.py`; moving the state into `auth_throttle` also
moved the logic somewhere it can be tested. The persistence helpers are
swapped for an in-memory stand-in — what is *not* covered here is the
cross-worker behaviour that motivated the move, because that needs more
than one process.
"""

from unittest import TestCase
from unittest.mock import patch

from app.business.mfa_throttle import mfa_lockout_seconds
from app.business.mfa_throttle import register_mfa_failure
from app.business.mfa_throttle import reset_mfa_throttle
from tests.app.business.auth_throttle_fake import AuthThrottleFake


_CEILING = 3
_LOCKOUT = 300
_USER = 7


class TestMfaThrottle(TestCase):

    def setUp(self):
        AuthThrottleFake().install('app.business.mfa_throttle')
        self.addCleanup(patch.stopall)

        # The ceilings are module constants, not configuration — patched
        # down here so a ceiling change doesn't quietly turn these into
        # tests of something else.
        for name, value in (('_MFA_FAIL_THRESHOLD', _CEILING),
                            ('_MFA_LOCKOUT_SECONDS', _LOCKOUT)):
            patch(f'app.business.mfa_throttle.{name}', value).start()

    def test_a_user_who_has_not_failed_is_not_throttled(self):
        self.assertEqual(0, mfa_lockout_seconds(_USER))

    def test_failures_below_the_ceiling_do_not_lock(self):
        for _ in range(_CEILING - 1):
            register_mfa_failure(_USER)

        self.assertEqual(0, mfa_lockout_seconds(_USER))

    def test_reaching_the_ceiling_locks_the_user_out(self):
        for _ in range(_CEILING):
            register_mfa_failure(_USER)

        self.assertGreater(mfa_lockout_seconds(_USER), 0)
        self.assertLessEqual(mfa_lockout_seconds(_USER), _LOCKOUT)

    def test_locking_one_user_leaves_another_alone(self):
        """The bucket is per account, so one user burning their attempts
        must not lock the next person out of the same server."""
        for _ in range(_CEILING):
            register_mfa_failure(_USER)

        self.assertEqual(0, mfa_lockout_seconds(_USER + 1))

    def test_a_successful_verify_clears_the_count(self):
        for _ in range(_CEILING - 1):
            register_mfa_failure(_USER)
        reset_mfa_throttle(_USER)

        for _ in range(_CEILING - 1):
            register_mfa_failure(_USER)

        self.assertEqual(0, mfa_lockout_seconds(_USER))

    def test_the_lockout_clears_the_count_with_it(self):
        """Otherwise the first attempt after a lockout expires re-locks the
        account immediately, and one burst costs every later window too."""
        for _ in range(_CEILING):
            register_mfa_failure(_USER)

        register_mfa_failure(_USER)

        # Still one short of the ceiling on the fresh count, so nothing
        # beyond the original lockout has been added.
        self.assertLessEqual(mfa_lockout_seconds(_USER), _LOCKOUT)
