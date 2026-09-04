#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for the password login throttle (VI-010).

`_client_key` reads `request.remote_addr`, so each test drives the helpers
inside a `test_request_context` with an explicit source address — that is
also how the two-bucket behaviour is exercised: same address, different
usernames, and vice versa.

The throttle keeps module-level state, so every test resets it first.
"""

from unittest import TestCase
from unittest.mock import patch

from app import app
from app.business.login_throttle import login_lockout_seconds
from app.business.login_throttle import register_login_failure
from app.business.login_throttle import register_login_success
from app.business.login_throttle import reset_login_throttle


_ACCOUNT_CEILING = 4
_CLIENT_CEILING = 10
_LOCKOUT = 300


def _context(address='203.0.113.7'):
    return app.test_request_context('/api/v2/auth/login',
                                    environ_base={'REMOTE_ADDR': address})


class TestLoginThrottle(TestCase):

    def setUp(self):
        reset_login_throttle()
        self.addCleanup(reset_login_throttle)
        # Small, explicit ceilings so a test doesn't depend on whatever the
        # deployment's config happens to be.
        overrides = {
            'LOGIN_MAX_ATTEMPTS': _ACCOUNT_CEILING,
            'LOGIN_MAX_ATTEMPTS_PER_CLIENT': _CLIENT_CEILING,
            'LOGIN_LOCKOUT_SECONDS': _LOCKOUT,
        }
        patcher = patch.dict(app.config, overrides)
        patcher.start()
        self.addCleanup(patcher.stop)

    # ---------- account bucket ----------

    def test_fresh_account_is_not_throttled(self):
        with _context():
            self.assertEqual(0, login_lockout_seconds('analyst'))

    def test_failures_below_the_ceiling_do_not_lock(self):
        with _context():
            for _ in range(_ACCOUNT_CEILING - 1):
                register_login_failure('analyst')
            self.assertEqual(0, login_lockout_seconds('analyst'))

    def test_reaching_the_ceiling_locks_the_account(self):
        with _context():
            for _ in range(_ACCOUNT_CEILING):
                register_login_failure('analyst')
            self.assertGreater(login_lockout_seconds('analyst'), 0)
            self.assertLessEqual(login_lockout_seconds('analyst'), _LOCKOUT)

    def test_lockout_is_case_insensitive_on_the_username(self):
        # Otherwise 'Analyst' / 'ANALYST' would each get their own budget
        # against the very same account.
        with _context():
            for _ in range(_ACCOUNT_CEILING):
                register_login_failure('Analyst')
            self.assertGreater(login_lockout_seconds('analyst'), 0)
            self.assertGreater(login_lockout_seconds('ANALYST'), 0)

    def test_success_clears_the_account_bucket(self):
        with _context():
            for _ in range(_ACCOUNT_CEILING - 1):
                register_login_failure('analyst')
            register_login_success('analyst')
            for _ in range(_ACCOUNT_CEILING - 1):
                register_login_failure('analyst')
            self.assertEqual(0, login_lockout_seconds('analyst'))

    def test_locking_one_account_leaves_another_alone(self):
        with _context():
            for _ in range(_ACCOUNT_CEILING):
                register_login_failure('analyst')
            self.assertGreater(login_lockout_seconds('analyst'), 0)
            self.assertEqual(0, login_lockout_seconds('other'))

    # ---------- client bucket ----------

    def test_spraying_many_accounts_locks_the_client(self):
        """The account ceiling never trips — one failure per username — but
        the source address still runs out of budget. This is the spraying
        case the second bucket exists for."""
        with _context('198.51.100.4'):
            for index in range(_CLIENT_CEILING):
                register_login_failure(f'user{index}')
            # A username never tried before is refused, because the address
            # is what is locked out.
            self.assertGreater(login_lockout_seconds('never-tried'), 0)

    def test_client_lockout_does_not_follow_another_address(self):
        with _context('198.51.100.4'):
            for index in range(_CLIENT_CEILING):
                register_login_failure(f'user{index}')
            self.assertGreater(login_lockout_seconds('never-tried'), 0)

        with _context('203.0.113.9'):
            self.assertEqual(0, login_lockout_seconds('never-tried'))

    def test_success_does_not_clear_the_client_bucket(self):
        """An attacker who lands one valid credential mid-spray must not get
        the address budget handed back."""
        with _context('198.51.100.4'):
            for index in range(_CLIENT_CEILING):
                register_login_failure(f'user{index}')
            register_login_success('user0')
            self.assertGreater(login_lockout_seconds('never-tried'), 0)

    # ---------- odd inputs ----------

    def test_missing_username_is_still_counted(self):
        # A client posting no username at all shouldn't get free attempts.
        with _context():
            for _ in range(_ACCOUNT_CEILING):
                register_login_failure(None)
            self.assertGreater(login_lockout_seconds(None), 0)

    def test_works_without_a_request_context(self):
        # Nothing in the module may explode if it is ever called outside a
        # request — the client bucket just collapses to a shared key.
        self.assertEqual(0, login_lockout_seconds('analyst'))
        register_login_failure('analyst')
        register_login_success('analyst')
