#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""`POST /api/v2/auth/refresh-token` and the MFA policy that changed underneath it.

Refresh used to carry `mfa_verified` forward unconditionally, which is
correct for the threat it was written against — a stolen *step-1* refresh
token must not upgrade itself — but wrong in the other direction. A token
minted while `enforce_mfa` was off carries `mfa_verified=True` by
construction, because `generate_auth_tokens` sets it whenever
`mfa_required` is False. Carrying that across a refresh once the policy is
on turns a session that has never seen a second factor into one that is
indistinguishable from a session that has, and renews it for the refresh
token's full fourteen days. It is also why enabling the toggle did nothing
to anyone already logged in.

The demotion that fixes it has to miss three cases and hit one, and the
expensive one to get wrong is OIDC: those users are exempt by design
because the IdP owns their second factor, and demoting them routes them to
`/auth/mfa-setup`, which demands the random password `login_routes`
generated for them and never disclosed. In an OIDC-only deployment that is
every user, with no way back. Hence `is_oidc` on the session row, and
hence the test below that pins it.

Driven through the real route with only the persistence layer and
`users_get_active` patched out, so the claims asserted here are the ones
`generate_auth_tokens` actually mints.
"""

from unittest import TestCase
from unittest.mock import MagicMock, patch

import jwt

from app import app


_SECRET = 'unit-test-secret'
_SID = 'sid-1'
_JTI = 'jti-current'


def _refresh_token(**overrides):
    claims = {
        'user_id': 7,
        'user_name': 'Alice Example',
        'user_email': 'alice@example.test',
        'user_login': 'alice',
        'type': 'refresh',
        'sid': _SID,
        'jti': _JTI,
        'mfa_required': False,
        'mfa_verified': True,
    }
    claims.update(overrides)
    return jwt.encode(claims, _SECRET, algorithm='HS256')


class TestRefreshMfaDemotion(TestCase):

    def setUp(self):
        self._config = patch.dict(app.config, {'SECRET_KEY': _SECRET})
        self._config.start()
        self.addCleanup(self._config.stop)

        self.session_row = MagicMock()
        self.session_row.user_id = 7
        self.session_row.refresh_jti = _JTI
        self.session_row.is_oidc = False

        patch('app.business.auth.user_auth_sessions_get_live',
              return_value=self.session_row).start()
        patch('app.business.auth.user_auth_sessions_rotate',
              return_value=True).start()
        patch('app.business.auth.demo_mode_blocks_mfa',
              return_value=False).start()
        self.policy = patch('app.business.auth.get_server_settings_enforce_mfa',
                            return_value=False).start()

        user = MagicMock()
        user.id = 7
        user.name = 'Alice Example'
        user.email = 'alice@example.test'
        user.user = 'alice'
        patch('app.blueprints.rest.v2.auth.users_get_active',
              return_value=user).start()
        self.addCleanup(patch.stopall)

        self.client = app.test_client()

    def _refresh(self, token):
        response = self.client.post('/api/v2/auth/refresh-token',
                                    json={'refresh_token': token})
        self.assertEqual(200, response.status_code)
        tokens = response.get_json()['tokens']
        return jwt.decode(tokens['access_token'], _SECRET, algorithms=['HS256'])

    def test_a_never_challenged_session_is_demoted_once_the_policy_is_on(self):
        """The laundering window, closed.

        The token was minted while MFA was off — nobody ever presented a
        second factor for it — so under an enabled policy it has to go
        back to step 1 rather than renew as fully verified.
        """
        self.policy.return_value = True

        claims = self._refresh(_refresh_token(mfa_required=False, mfa_verified=True))

        self.assertTrue(claims['mfa_required'])
        self.assertFalse(claims['mfa_verified'])

    def test_an_oidc_session_survives_the_policy_being_turned_on(self):
        """The lockout this must not cause.

        OIDC users have no usable password, so a demotion sends them to an
        enrollment screen they can never complete. Their second factor is
        the IdP's business and IRIS has to leave them alone.
        """
        self.session_row.is_oidc = True
        self.policy.return_value = True

        claims = self._refresh(_refresh_token(mfa_required=False, mfa_verified=True))

        self.assertTrue(claims['mfa_verified'])

    def test_a_step_one_token_still_stays_step_one(self):
        """The original guarantee, unchanged: a stolen step-1 refresh token
        must not be laundered into a verified one by refreshing it."""
        self.policy.return_value = True

        claims = self._refresh(_refresh_token(mfa_required=True, mfa_verified=False))

        self.assertTrue(claims['mfa_required'])
        self.assertFalse(claims['mfa_verified'])

    def test_a_verified_session_is_not_logged_out_every_fifteen_minutes(self):
        """An enrolled user under a steady policy has `mfa_required=True`
        on their token, so the demotion never looks at them."""
        self.policy.return_value = True

        claims = self._refresh(_refresh_token(mfa_required=True, mfa_verified=True))

        self.assertTrue(claims['mfa_verified'])

    def test_nothing_is_demoted_while_the_policy_stays_off(self):
        claims = self._refresh(_refresh_token(mfa_required=False, mfa_verified=True))

        self.assertFalse(claims['mfa_required'])
        self.assertTrue(claims['mfa_verified'])
