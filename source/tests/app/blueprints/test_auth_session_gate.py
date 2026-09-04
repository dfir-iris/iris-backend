#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for the per-request session gate on bearer tokens (VI-004).

Two decorators admit JWTs and they do not share code: `ac_api_requires`
goes through `_token_authentication_process`, while `@api_auth()` — which
guards `/auth/whoami` and `/auth/logout` — decodes the token itself in
`api_auth._jwt_user`. A revoked session has to be refused on both, so
both are covered here.

The DB is patched out at the business-layer boundary
(`auth_session_is_live` / `validate_auth_token`'s session lookup), which
is exactly the seam the production code uses.
"""

from unittest import TestCase
from unittest.mock import MagicMock, patch

import jwt
from flask import g
from flask import request

from app import app
from app.blueprints.access_controls import _token_authentication_process
from app.blueprints.rest.api_auth import _jwt_user


_SECRET = 'unit-test-secret'


def _token(claims):
    return jwt.encode(claims, _SECRET, algorithm='HS256')


def _access_claims(**overrides):
    claims = {
        'user_id': 7,
        'user_login': 'alice',
        'user_name': 'Alice Example',
        'user_email': 'alice@example.test',
        'type': 'access',
        'sid': 'sid-1',
        'mfa_required': False,
        'mfa_verified': True,
    }
    claims.update(overrides)
    return claims


class _GateTestCase(TestCase):

    def setUp(self):
        self._config = patch.dict(app.config, {'SECRET_KEY': _SECRET})
        self._config.start()
        self.addCleanup(self._config.stop)

        self.session_row = MagicMock()
        self.get_live = patch('app.business.auth.user_auth_sessions_get_live',
                              return_value=self.session_row).start()
        self.addCleanup(patch.stopall)

    def _headers(self, claims):
        return {'Authorization': f'Bearer {_token(claims)}'}


class TestAcApiRequiresGate(_GateTestCase):
    """`_token_authentication_process`, the gate behind `ac_api_requires`."""

    def _authenticate(self, claims):
        with app.test_request_context('/', headers=self._headers(claims)):
            admitted = _token_authentication_process(request)
            return admitted, (g.auth_user if 'auth_user' in g else None)

    def test_token_of_a_live_session_is_admitted(self):
        admitted, auth_user = self._authenticate(_access_claims())
        self.assertTrue(admitted)
        self.assertEqual('sid-1', auth_user['session_id'])

    def test_token_of_a_revoked_session_is_refused(self):
        """The finding in one assertion: after logout, a still-signed and
        still-unexpired access token must stop authenticating."""
        self.get_live.return_value = None
        admitted, _ = self._authenticate(_access_claims())
        self.assertFalse(admitted)

    def test_token_minted_before_sessions_existed_is_refused(self):
        claims = _access_claims()
        del claims['sid']
        admitted, _ = self._authenticate(claims)
        self.assertFalse(admitted)

    def test_step1_token_of_a_live_session_is_still_refused(self):
        # VI-003 must keep working now that a second reason to refuse
        # sits next to it.
        admitted, _ = self._authenticate(
            _access_claims(mfa_required=True, mfa_verified=False)
        )
        self.assertFalse(admitted)


class TestApiAuthGate(_GateTestCase):
    """`_jwt_user`, the gate behind `@api_auth()` on whoami / logout."""

    def setUp(self):
        super().setUp()
        self.user = MagicMock()
        patch('app.blueprints.rest.api_auth._safe_get_active',
              return_value=self.user).start()

    def _resolve(self, claims):
        with app.test_request_context('/', headers=self._headers(claims)):
            resolved = _jwt_user()
            return resolved, (g.auth_session_id if 'auth_session_id' in g else None)

    def test_token_of_a_live_session_resolves_the_user(self):
        resolved, session_id = self._resolve(_access_claims())
        self.assertIs(self.user, resolved)
        # logout reads this to know which family to close.
        self.assertEqual('sid-1', session_id)

    def test_token_of_a_revoked_session_is_invalid(self):
        self.get_live.return_value = None
        resolved, _ = self._resolve(_access_claims())
        # 'invalid' rather than None: falling through to session auth
        # would hand the caller back the access they just gave up.
        self.assertEqual('invalid', resolved)

    def test_token_minted_before_sessions_existed_is_invalid(self):
        claims = _access_claims()
        del claims['sid']
        resolved, _ = self._resolve(claims)
        self.assertEqual('invalid', resolved)

    def test_a_non_jwt_bearer_is_rejected_before_the_session_lookup(self):
        """API keys arrive in this same header and are not JWTs. They fail
        at the decode, exactly as they did before sessions existed — the
        session gate sits behind that and must not alter the API-key
        path, which the whole black-box suite authenticates with."""
        with app.test_request_context('/', headers={'Authorization': 'Bearer not-a-jwt'}):
            self.assertEqual('invalid', _jwt_user())
        self.get_live.assert_not_called()

    def test_no_authorization_header_is_not_a_token_request(self):
        with app.test_request_context('/'):
            self.assertIsNone(_jwt_user())
