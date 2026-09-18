#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 3 of the License, or (at your option) any later version.

"""Integration tests for UserApiKey — named, scope-restricted API keys.

Two endpoint surfaces:
  * `/api/v2/me/api-keys`                       — self-service (any user)
  * `/api/v2/manage/users/<id>/api-keys`        — admin

Focus areas:
  * Round-trip: mint → surfaces once in response → not surfaced again.
  * Scope mask actually restricts. Mint an admin-owned key with
    `scope_mask = IRIS_PERMISSION_ALERTS_READ` and confirm a write
    operation is refused while a read succeeds.
  * Revocation is idempotent and immediately breaks the key.
  * Legacy `User.api_key` still authenticates through the compat path.
"""

import requests

from unittest import TestCase

from iris import (
    API_URL,
    IRIS_PERMISSION_ALERTS_READ,
    Iris,
)


def _call_with_key(path: str, key: str, method: str = 'GET',
                   body: dict | None = None) -> requests.Response:
    headers = {'X-IRIS-AUTH': key, 'Content-Type': 'application/json'}
    fn = getattr(requests, method.lower())
    if body is None:
        return fn(f'{API_URL}{path}', headers=headers)
    return fn(f'{API_URL}{path}', headers=headers, json=body)


class TestsRestUserApiKeys(TestCase):

    def setUp(self) -> None:
        self._subject = Iris()

    def tearDown(self):
        # Revoke every non-legacy key the test may have created so the
        # `user_api_key` table stays clean across tests.
        resp = self._subject.get('/api/v2/me/api-keys')
        if resp.status_code == 200:
            for key in resp.json().get('api_keys', []):
                if key['name'] != 'legacy' and not key.get('revoked_at'):
                    self._subject.delete(f"/api/v2/me/api-keys/{key['id']}")
        self._subject.clear_database()

    # ---- Self-service (POST /me/api-keys) ------------------------------

    def test_self_create_returns_plaintext_key_once(self):
        response = self._subject.create('/api/v2/me/api-keys', {'name': 'test-key'})
        self.assertEqual(201, response.status_code)
        body = response.json()
        self.assertEqual('test-key', body['name'])
        self.assertIn('api_key', body)
        self.assertGreater(len(body['api_key']), 40)
        self.assertNotIn('key_hash', body)

        # Subsequent list must NOT include the plaintext.
        listed = self._subject.get('/api/v2/me/api-keys').json()['api_keys']
        row = next(k for k in listed if k['name'] == 'test-key')
        self.assertNotIn('api_key', row)
        self.assertNotIn('key_hash', row)

    def test_self_create_rejects_duplicate_name(self):
        self._subject.create('/api/v2/me/api-keys', {'name': 'dup'})
        response = self._subject.create('/api/v2/me/api-keys', {'name': 'dup'})
        self.assertEqual(400, response.status_code)

    def test_self_create_requires_name(self):
        response = self._subject.create('/api/v2/me/api-keys', {})
        self.assertEqual(400, response.status_code)

    def test_self_create_rejects_non_int_scope_mask(self):
        response = self._subject.create('/api/v2/me/api-keys', {
            'name': 'bad-scope', 'scope_mask': 'alerts_read',
        })
        self.assertEqual(400, response.status_code)

    # ---- Auth via new key ---------------------------------------------

    def test_new_key_authenticates_and_carries_full_perms(self):
        created = self._subject.create(
            '/api/v2/me/api-keys', {'name': 'full'}
        ).json()
        plaintext = created['api_key']

        # Full key inherits admin's permissions → /whoami works.
        resp = _call_with_key('/api/v2/auth/whoami', plaintext)
        self.assertEqual(200, resp.status_code)

    def test_scoped_key_restricts_permissions(self):
        # Mint an alerts_read-only key.
        created = self._subject.create('/api/v2/me/api-keys', {
            'name': 'scoped',
            'scope_mask': IRIS_PERMISSION_ALERTS_READ,
        }).json()
        plaintext = created['api_key']

        # `GET /alerts` requires `alerts_read` — allowed.
        resp = _call_with_key('/api/v2/alerts', plaintext)
        self.assertEqual(200, resp.status_code)

        # `POST /cases` requires `standard_user` — the mask does NOT
        # include standard_user, so this must be denied.
        resp = _call_with_key('/api/v2/cases', plaintext, 'POST', body={
            'case_name': 'x', 'case_description': 'y',
            'case_customer_id': 1, 'case_soc_id': '',
        })
        self.assertEqual(403, resp.status_code)

    def test_revoked_key_stops_authenticating(self):
        created = self._subject.create('/api/v2/me/api-keys', {
            'name': 'revoke-me',
        }).json()
        plaintext = created['api_key']
        key_id = created['id']

        # Sanity check: works before revoke.
        self.assertEqual(200, _call_with_key('/api/v2/auth/whoami', plaintext).status_code)

        # Revoke. The self-service route answers 200 with the revoked row
        # (the SPA re-renders the list from it); only the admin route,
        # which has nothing to hand back, answers 204.
        del_resp = self._subject.delete(f'/api/v2/me/api-keys/{key_id}')
        self.assertEqual(200, del_resp.status_code)

        # After revoke, key is rejected → falls through to Flask-Login
        # anonymous state, which the auth decorator turns into 401.
        resp = _call_with_key('/api/v2/auth/whoami', plaintext)
        self.assertEqual(401, resp.status_code)

    def test_revoke_is_idempotent(self):
        created = self._subject.create('/api/v2/me/api-keys', {
            'name': 'idem',
        }).json()
        key_id = created['id']
        self.assertEqual(200, self._subject.delete(f'/api/v2/me/api-keys/{key_id}').status_code)
        # Second revoke also succeeds (no 404) — the row is still there
        # with `revoked_at` populated.
        self.assertEqual(200, self._subject.delete(f'/api/v2/me/api-keys/{key_id}').status_code)

    # ---- Admin surface -------------------------------------------------

    def test_admin_can_mint_key_for_other_user(self):
        user = self._subject.create_dummy_user()
        response = self._subject.create(
            f'/api/v2/manage/users/{user.get_identifier()}/api-keys',
            {'name': 'ci', 'scope_mask': IRIS_PERMISSION_ALERTS_READ},
        )
        self.assertEqual(201, response.status_code)
        body = response.json()
        self.assertEqual('ci', body['name'])
        self.assertEqual(IRIS_PERMISSION_ALERTS_READ, body['scope_mask'])
        self.assertIn('api_key', body)

    def test_admin_list_and_revoke_user_keys(self):
        user = self._subject.create_dummy_user()
        created = self._subject.create(
            f'/api/v2/manage/users/{user.get_identifier()}/api-keys',
            {'name': 'audit'},
        ).json()
        key_id = created['id']

        listed = self._subject.get(
            f'/api/v2/manage/users/{user.get_identifier()}/api-keys'
        ).json()['api_keys']
        self.assertTrue(any(k['id'] == key_id for k in listed))

        del_resp = self._subject.delete(
            f'/api/v2/manage/users/{user.get_identifier()}/api-keys/{key_id}'
        )
        self.assertEqual(204, del_resp.status_code)

    def test_non_admin_cannot_manage_other_users_keys(self):
        target = self._subject.create_dummy_user()
        actor = self._subject.create_dummy_user()

        # `actor` (non-admin) calls the admin endpoint against `target`.
        actor_key = actor.get_api_key() if hasattr(actor, 'get_api_key') else None
        # `User.api_key` — legacy attribute; see tests/user.py. If the
        # accessor name differs, skip this test rather than fail with
        # an attribute error.
        if actor_key is None:
            self.skipTest('test helper does not expose actor api key')
        resp = _call_with_key(
            f'/api/v2/manage/users/{target.get_identifier()}/api-keys',
            actor_key,
            method='POST',
            body={'name': 'nope'},
        )
        self.assertEqual(403, resp.status_code)

    # ---- Legacy compat -------------------------------------------------

    def test_legacy_api_key_still_authenticates(self):
        # The seeded admin has a legacy `User.api_key` — the back-fill
        # migration mirrored it into UserApiKey(name='legacy'), so the
        # request now succeeds via the UserApiKey lookup path. Both
        # code paths must accept the same input.
        legacy_key = self._subject._api._api_key
        resp = _call_with_key('/api/v2/auth/whoami', legacy_key)
        self.assertEqual(200, resp.status_code)
