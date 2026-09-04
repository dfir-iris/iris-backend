#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for the server-side auth sessions in business/auth.py (VI-004).

The whole point of the change is that a token is no longer sufficient on
its own: it has to name a session row that is still open, and a refresh
token has to be the *current* one for that session. These tests pin both
halves plus the reuse-detection response, with the persistence layer
patched out so nothing touches a database.

`mfa_is_enforced` and `track_activity` are patched for the same reason —
one reads server settings, the other writes an activity row.
"""

from unittest import TestCase
from unittest.mock import MagicMock, patch

import jwt

from app import app
from app.business.auth import auth_session_accepts_refresh
from app.business.auth import auth_session_is_live
from app.business.auth import auth_session_revoke
from app.business.auth import generate_auth_tokens
from app.business.auth import validate_auth_token
from app.models.errors import BusinessProcessingError


_SECRET = 'unit-test-secret'


def _user(user_id=7):
    user = MagicMock()
    user.id = user_id
    user.name = 'Alice Example'
    user.email = 'alice@example.test'
    user.user = 'alice'
    return user


def _decode(token):
    return jwt.decode(token, _SECRET, algorithms=['HS256'])


def _session_row(user_id=7, refresh_jti='jti-current'):
    row = MagicMock()
    row.user_id = user_id
    row.refresh_jti = refresh_jti
    return row


class _AuthSessionTestCase(TestCase):
    """Patches every DB-touching collaborator of business/auth.py."""

    def setUp(self):
        self._config = patch.dict(app.config, {'SECRET_KEY': _SECRET})
        self._config.start()
        self.addCleanup(self._config.stop)

        self.enforced = patch('app.business.auth.mfa_is_enforced', return_value=False)
        self.enforced.start()
        self.addCleanup(self.enforced.stop)

        self.track = patch('app.business.auth.track_activity')
        self.tracked = self.track.start()
        self.addCleanup(self.track.stop)

        self.create = patch('app.business.auth.user_auth_sessions_create',
                            return_value='sid-new').start()
        self.get_live = patch('app.business.auth.user_auth_sessions_get_live',
                              return_value=_session_row()).start()
        self.rotate = patch('app.business.auth.user_auth_sessions_rotate',
                            return_value=True).start()
        self.revoke = patch('app.business.auth.user_auth_sessions_revoke',
                            return_value=True).start()
        self.addCleanup(patch.stopall)


class TestGenerateAuthTokens(_AuthSessionTestCase):

    def test_opens_a_session_when_none_is_given(self):
        tokens = generate_auth_tokens(_user())

        self.create.assert_called_once()
        self.rotate.assert_not_called()
        self.assertEqual('sid-new', _decode(tokens['access_token'])['sid'])
        self.assertEqual('sid-new', _decode(tokens['refresh_token'])['sid'])

    def test_refresh_token_carries_the_jti_that_was_stored(self):
        tokens = generate_auth_tokens(_user())

        stored_jti = self.create.call_args.args[1]
        self.assertEqual(stored_jti, _decode(tokens['refresh_token'])['jti'])

    def test_access_token_carries_no_jti(self):
        # Only refresh tokens are rotated, so only they need an identity
        # of their own. A `jti` on the access token would suggest it is
        # single-use, which it is not.
        tokens = generate_auth_tokens(_user())
        self.assertNotIn('jti', _decode(tokens['access_token']))

    def test_rotating_reuses_the_session_and_mints_a_fresh_jti(self):
        first = generate_auth_tokens(_user())
        first_jti = _decode(first['refresh_token'])['jti']

        second = generate_auth_tokens(_user(), session_id='sid-existing')
        second_jti = _decode(second['refresh_token'])['jti']

        self.assertEqual('sid-existing', _decode(second['refresh_token'])['sid'])
        self.assertNotEqual(first_jti, second_jti)
        self.rotate.assert_called_once_with('sid-existing', second_jti)

    def test_rotating_a_dead_session_refuses_rather_than_reopening(self):
        """The containment guarantee: once a family is revoked, nothing
        may mint into it again — least of all by silently starting a new
        one and handing back a working credential."""
        self.rotate.return_value = False

        with self.assertRaises(BusinessProcessingError):
            generate_auth_tokens(_user(), session_id='sid-revoked')

        self.create.assert_not_called()

    def test_mfa_claims_are_untouched_by_session_binding(self):
        # VI-003 relies on this exact claim pair; adding `sid`/`jti` must
        # not disturb it.
        self.enforced.stop()
        with patch('app.business.auth.mfa_is_enforced', return_value=True):
            step_one = generate_auth_tokens(_user())
            verified = generate_auth_tokens(_user(), mfa_verified=True)
        self.enforced.start()

        for token in (step_one['access_token'], step_one['refresh_token']):
            self.assertTrue(_decode(token)['mfa_required'])
            self.assertFalse(_decode(token)['mfa_verified'])

        for token in (verified['access_token'], verified['refresh_token']):
            self.assertTrue(_decode(token)['mfa_required'])
            self.assertTrue(_decode(token)['mfa_verified'])


class TestValidateAuthToken(_AuthSessionTestCase):

    def test_token_of_a_live_session_is_accepted(self):
        tokens = generate_auth_tokens(_user())
        data = validate_auth_token(tokens['access_token'])

        self.assertIsNotNone(data)
        self.assertEqual(7, data['user_id'])
        self.assertEqual('sid-new', data['session_id'])

    def test_token_of_a_revoked_session_is_rejected(self):
        """Logout's teeth: the access token is still signed and still
        inside its fifteen-minute window, and it must stop working
        anyway."""
        tokens = generate_auth_tokens(_user())
        self.get_live.return_value = None

        self.assertIsNone(validate_auth_token(tokens['access_token']))

    def test_token_without_a_sid_is_rejected(self):
        """Pre-upgrade tokens are not grandfathered — that is the whole
        point of the fix, and it costs everyone one re-login."""
        legacy = jwt.encode({'user_id': 7, 'type': 'access'}, _SECRET, algorithm='HS256')
        self.assertIsNone(validate_auth_token(legacy))

    def test_refresh_token_is_still_rejected_as_an_access_token(self):
        tokens = generate_auth_tokens(_user())
        self.assertIsNone(validate_auth_token(tokens['refresh_token']))


class TestAuthSessionAcceptsRefresh(_AuthSessionTestCase):

    def test_current_refresh_token_is_accepted_without_side_effects(self):
        self.assertTrue(auth_session_accepts_refresh('sid-1', 'jti-current'))
        self.revoke.assert_not_called()

    def test_unknown_or_revoked_session_is_refused(self):
        self.get_live.return_value = None

        self.assertFalse(auth_session_accepts_refresh('sid-1', 'jti-current'))
        # Nothing to revoke, and no reuse to report: the family is
        # already closed.
        self.revoke.assert_not_called()
        self.tracked.assert_not_called()

    def test_replayed_refresh_token_kills_the_whole_family(self):
        """A `jti` we already rotated away from can only come from a copy
        of a spent token. We cannot tell the thief from the victim, so
        both lose the session."""
        self.assertFalse(auth_session_accepts_refresh('sid-1', 'jti-previous'))

        self.revoke.assert_called_once_with('sid-1')
        self.tracked.assert_called_once()

    def test_missing_jti_is_treated_as_a_replay(self):
        self.assertFalse(auth_session_accepts_refresh('sid-1', None))
        self.revoke.assert_called_once_with('sid-1')

    def test_missing_sid_is_refused_without_a_lookup(self):
        self.assertFalse(auth_session_accepts_refresh(None, 'jti-current'))
        self.get_live.assert_not_called()


class TestAuthSessionHelpers(_AuthSessionTestCase):

    def test_is_live_follows_the_session_row(self):
        self.assertTrue(auth_session_is_live('sid-1'))
        self.get_live.return_value = None
        self.assertFalse(auth_session_is_live('sid-1'))

    def test_is_live_refuses_an_absent_session_id_without_a_lookup(self):
        self.assertFalse(auth_session_is_live(None))
        self.assertFalse(auth_session_is_live(''))
        self.get_live.assert_not_called()

    def test_revoke_without_a_session_id_is_a_no_op(self):
        # Cookie and API-key callers have no session to close; logout
        # must not blow up on them.
        self.assertFalse(auth_session_revoke(None))
        self.revoke.assert_not_called()

    def test_revoke_closes_the_named_family(self):
        self.assertTrue(auth_session_revoke('sid-1'))
        self.revoke.assert_called_once_with('sid-1')
