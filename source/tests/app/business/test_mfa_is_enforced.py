#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for `mfa_is_enforced` — the policy read itself.

`test_auth_mfa_required.py` covers what the rest of the auth code does
once it knows the policy, and it does that by patching `mfa_is_enforced`
out. Which means nothing anywhere asserted that the policy is *read*
correctly, and that is exactly where the defect was: the answer was
cached on `app.config`, per process, with no expiry. Under `gunicorn -w 4`
the four workers' views diverged permanently the moment an admin enabled
the toggle, because the only refresh path was the settings `PUT` and that
lands on one worker. A worker still holding `False` minted tokens flagged
`mfa_verified=True`.

So the assertion that matters here is not "it returns what the row says"
but "it asks, every single time". `test_policy_is_re_read_on_every_call`
is the regression guard: it fails against any cache, including a TTL one,
because a stale answer good for even a second is good enough to mint a
fourteen-day credential.

No DB and no request context — the persistence helper is patched out.
"""

from unittest import TestCase
from unittest.mock import patch

from app.business.auth import mfa_is_enforced


class TestMfaIsEnforced(TestCase):

    def setUp(self):
        self.demo = patch('app.business.auth.demo_mode_blocks_mfa',
                          return_value=False).start()
        self.read_policy = patch(
            'app.business.auth.get_server_settings_enforce_mfa',
            return_value=False).start()
        self.addCleanup(patch.stopall)

    def test_follows_the_stored_policy(self):
        self.assertFalse(mfa_is_enforced())

        self.read_policy.return_value = True
        self.assertTrue(mfa_is_enforced())

    def test_policy_is_re_read_on_every_call(self):
        """The one that would have caught it.

        An admin enabling MFA has to be visible to the very next call, on
        every worker. Anything that remembers the first answer fails here.
        """
        self.read_policy.return_value = False
        self.assertFalse(mfa_is_enforced())

        # The admin flips the toggle. No process was restarted and nothing
        # told this worker about it — it has to go and look.
        self.read_policy.return_value = True
        self.assertTrue(mfa_is_enforced())

        # And back off again, for the same reason in reverse: a deployment
        # that turns MFA off must not keep prompting for it.
        self.read_policy.return_value = False
        self.assertFalse(mfa_is_enforced())

        self.assertEqual(3, self.read_policy.call_count)

    def test_demo_mode_wins_over_an_enabled_policy(self):
        """Demo accounts are shared, so a second factor bound to one
        visitor's authenticator locks every other visitor out."""
        self.read_policy.return_value = True
        self.demo.return_value = True

        self.assertFalse(mfa_is_enforced())

    def test_demo_mode_short_circuits_before_the_read(self):
        self.demo.return_value = True

        mfa_is_enforced()

        self.read_policy.assert_not_called()
