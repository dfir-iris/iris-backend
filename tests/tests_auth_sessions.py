#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 3 of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
#  Lesser General Public License for more details.

"""
Integration tests for refresh-token rotation and session revocation (VI-004).

These run against the docker-compose stack the rest of the suite uses and
drive `/api/v2/auth/*` end to end, so they exercise the real JWT claims,
the `user_auth_session` rows behind them, and the per-request gate in
`_token_authentication_process`.

What the finding was, restated as behaviour these tests pin:

  - a refresh token used to be replayable for its full fourteen days,
    in parallel with the victim's own use. It is now single-use, and
    presenting a spent one revokes the entire family.
  - `/auth/logout` used to clear a cookie and revoke nothing, leaving
    the caller's access token good for up to another fifteen minutes.
    It now closes the session, and the access token dies with it.

Response shape note (same as tests_auth_mfa): the v2 auth endpoints use
`response_api_success`, which serialises the data dict DIRECTLY as the
body — `tokens` sits at the top level, not under `body['data']`. Errors
are HTTP 400 with `{message: "..."}`.

Requests are deliberately sent WITHOUT cookies. `_read_refresh_token`
falls back to the `iris_rt` cookie when the body has no token, and a
`requests.Session` would let a cookie set by an earlier call silently
stand in for the token under test.
"""

from unittest import TestCase
from uuid import uuid4

import requests
from urllib import parse

from iris import Iris
from iris import API_URL


_PASSWORD = 'aA.1234567890'


def _login(username, password):
    url = parse.urljoin(API_URL, '/api/v2/auth/login')
    return requests.post(url, json={'username': username, 'password': password}).json()


def _refresh(refresh_token):
    url = parse.urljoin(API_URL, '/api/v2/auth/refresh-token')
    return requests.post(url, json={'refresh_token': refresh_token})


def _logout(access_token):
    url = parse.urljoin(API_URL, '/api/v2/auth/logout')
    return requests.post(url, headers={'Authorization': f'Bearer {access_token}'},
                         allow_redirects=False)


def _get_with_bearer(path, access_token):
    url = parse.urljoin(API_URL, path)
    return requests.get(url, headers={'Authorization': f'Bearer {access_token}'})


class TestsAuthSessions(TestCase):

    def setUp(self) -> None:
        self._subject = Iris()

    def tearDown(self):
        self._subject.clear_database()

    def _logged_in_user(self):
        """Create a user, log in, return its token pair."""
        user_name = f'user{uuid4()}'
        self._subject.create_user(user_name, _PASSWORD)
        return _login(user_name, _PASSWORD)['tokens']

    # ---------- rotation ----------

    def test_refresh_should_return_a_different_refresh_token(self):
        tokens = self._logged_in_user()
        rotated = _refresh(tokens['refresh_token'])
        self.assertEqual(200, rotated.status_code)
        self.assertNotEqual(tokens['refresh_token'],
                            rotated.json()['tokens']['refresh_token'])

    def test_rotated_refresh_token_should_admit_the_new_access_token(self):
        """Rotation must not cost the user their session — the pair that
        comes back has to work."""
        tokens = self._logged_in_user()
        rotated = _refresh(tokens['refresh_token']).json()['tokens']
        self.assertEqual(200, _get_with_bearer('/api/v2/cases',
                                               rotated['access_token']).status_code)

    def test_new_refresh_token_should_itself_be_refreshable(self):
        """Chained rotation: three hops must all succeed, or the SPA
        breaks after its second access-token expiry."""
        refresh_token = self._logged_in_user()['refresh_token']
        for _ in range(3):
            response = _refresh(refresh_token)
            self.assertEqual(200, response.status_code)
            refresh_token = response.json()['tokens']['refresh_token']

    # ---------- replay ----------

    def test_spent_refresh_token_should_be_refused(self):
        """The finding itself. Before the fix this second exchange
        returned a fresh, fully valid token pair to whoever held the
        copy."""
        tokens = self._logged_in_user()
        self.assertEqual(200, _refresh(tokens['refresh_token']).status_code)

        replayed = _refresh(tokens['refresh_token'])
        self.assertEqual(400, replayed.status_code)
        self.assertEqual('Invalid refresh token', replayed.json().get('message'))

    def test_replay_should_revoke_the_whole_family(self):
        """Reuse detection. Once a spent token is presented we cannot tell
        the thief from the victim, so the legitimate holder's current
        token must stop working too — that is what actually contains the
        theft rather than merely inconveniencing the attacker."""
        tokens = self._logged_in_user()
        current = _refresh(tokens['refresh_token']).json()['tokens']

        # The attacker replays the token the victim already spent.
        self.assertEqual(400, _refresh(tokens['refresh_token']).status_code)

        # The victim's own, up-to-date credentials are now dead as well.
        self.assertEqual(400, _refresh(current['refresh_token']).status_code)
        self.assertEqual(401, _get_with_bearer('/api/v2/cases',
                                               current['access_token']).status_code)

    def test_replay_should_not_affect_another_session_of_the_same_user(self):
        """Revocation is per token family, not per user. A second browser
        the user is legitimately signed in on must survive."""
        user_name = f'user{uuid4()}'
        self._subject.create_user(user_name, _PASSWORD)
        first = _login(user_name, _PASSWORD)['tokens']
        second = _login(user_name, _PASSWORD)['tokens']

        _refresh(first['refresh_token'])
        self.assertEqual(400, _refresh(first['refresh_token']).status_code)

        self.assertEqual(200, _get_with_bearer('/api/v2/cases',
                                               second['access_token']).status_code)
        self.assertEqual(200, _refresh(second['refresh_token']).status_code)

    # ---------- logout ----------

    def test_logout_should_revoke_the_access_token(self):
        """A logout that leaves the bearer token working for another
        fifteen minutes is not a logout."""
        tokens = self._logged_in_user()
        self.assertEqual(200, _get_with_bearer('/api/v2/cases',
                                               tokens['access_token']).status_code)

        _logout(tokens['access_token'])

        self.assertEqual(401, _get_with_bearer('/api/v2/cases',
                                               tokens['access_token']).status_code)

    def test_logout_should_revoke_the_refresh_token(self):
        """Otherwise the caller simply refreshes their way back in."""
        tokens = self._logged_in_user()
        _logout(tokens['access_token'])

        response = _refresh(tokens['refresh_token'])
        self.assertEqual(400, response.status_code)
        self.assertEqual('Invalid refresh token', response.json().get('message'))

    def test_logout_should_not_affect_another_session_of_the_same_user(self):
        user_name = f'user{uuid4()}'
        self._subject.create_user(user_name, _PASSWORD)
        first = _login(user_name, _PASSWORD)['tokens']
        second = _login(user_name, _PASSWORD)['tokens']

        _logout(first['access_token'])

        self.assertEqual(200, _get_with_bearer('/api/v2/cases',
                                               second['access_token']).status_code)

    # ---------- api keys are a separate mechanism ----------

    def test_api_key_authentication_should_be_unaffected(self):
        """API keys carry no JWT and no session row. The gate added on the
        bearer path must not touch them — the whole black-box suite
        authenticates this way."""
        self.assertEqual(200, self._subject.get('/api/v2/cases').status_code)
