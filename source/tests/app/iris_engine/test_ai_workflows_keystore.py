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
#
#  You should have received a copy of the GNU Lesser General Public License
#  along with this program; if not, write to the Free Software Foundation,
#  Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.

"""Keystore resolution for workflow runs: resolution order, group gating,
masking, the LLM view and the host allowlist.

The query helpers are patched; entries are plain namespaces, nothing
touches the database.
"""

import base64
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
from urllib.parse import quote

from app.iris_engine.ai_workflows.keystore import KeystoreError
from app.iris_engine.ai_workflows.keystore import ai_workflows_keystore_host_matches
from app.iris_engine.ai_workflows.keystore import ai_workflows_keystore_resolver
from app.iris_engine.ai_workflows.keystore import ai_workflows_keystore_visible_entries
from app.iris_engine.mail.secrets import encrypt_secret
from app.iris_engine.webhooks.render import MASK

_MODULE = 'app.iris_engine.ai_workflows.keystore'
_USER_ID = 7
_OTHER_USER_ID = 8


def _entry(entry_id, name, value, scope='personal', owner_id=_USER_ID, is_secret=True,
           allowed_group_ids=None, allowed_hosts=None):
    return SimpleNamespace(
        id=entry_id,
        name=name,
        value=encrypt_secret(value) if is_secret else value,
        is_secret=is_secret,
        scope=scope,
        owner_id=owner_id if scope == 'personal' else None,
        allowed_group_ids=allowed_group_ids or [],
        allowed_hosts=allowed_hosts or [],
        last_used_at=None,
    )


class _KeystoreTestCase(TestCase):

    def setUp(self):
        self._entries = []
        self._group_ids = []
        patchers = [
            patch(f'{_MODULE}.ai_workflows_db_keystore_list', side_effect=lambda **_kw: list(self._entries)),
            patch(f'{_MODULE}.ai_workflows_db_user_group_ids', side_effect=lambda _uid: list(self._group_ids)),
            patch(f'{_MODULE}.ai_workflows_db_keystore_touch', side_effect=self._touch),
        ]
        self._touched = []
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def _touch(self, entry):
        entry.last_used_at = 'now'
        self._touched.append(entry.id)


class TestKeystoreResolution(_KeystoreTestCase):

    def test_personal_entry_should_win_over_shared_entry(self):
        self._entries = [
            _entry(1, 'TOKEN', 'shared-value', scope='shared'),
            _entry(2, 'TOKEN', 'personal-value'),
        ]
        self.assertEqual('personal-value', ai_workflows_keystore_resolver(_USER_ID).get('TOKEN'))

    def test_shared_entry_should_be_used_without_personal_entry(self):
        self._entries = [_entry(1, 'TOKEN', 'shared-value', scope='shared')]
        self.assertEqual('shared-value', ai_workflows_keystore_resolver(_USER_ID).get('TOKEN'))

    def test_personal_entry_of_another_user_should_not_resolve(self):
        self._entries = [_entry(1, 'TOKEN', 'theirs', owner_id=_OTHER_USER_ID)]
        with self.assertRaises(KeystoreError):
            ai_workflows_keystore_resolver(_USER_ID).get('TOKEN')

    def test_shared_entry_limited_to_other_groups_should_not_resolve(self):
        self._group_ids = [3]
        self._entries = [_entry(1, 'TOKEN', 'v', scope='shared', allowed_group_ids=[4, 5])]
        with self.assertRaises(KeystoreError):
            ai_workflows_keystore_resolver(_USER_ID).get('TOKEN')

    def test_shared_entry_limited_to_a_group_of_the_user_should_resolve(self):
        self._group_ids = [3, 5]
        self._entries = [_entry(1, 'TOKEN', 'value', scope='shared', allowed_group_ids=[4, 5])]
        self.assertEqual('value', ai_workflows_keystore_resolver(_USER_ID).get('TOKEN'))

    def test_visible_entries_should_apply_ownership_and_groups(self):
        self._group_ids = [3]
        mine = _entry(1, 'MINE', 'v')
        theirs = _entry(2, 'THEIRS', 'v', owner_id=_OTHER_USER_ID)
        open_shared = _entry(3, 'OPEN', 'v', scope='shared')
        my_group = _entry(4, 'MY_GROUP', 'v', scope='shared', allowed_group_ids=[3])
        other_group = _entry(5, 'OTHER_GROUP', 'v', scope='shared', allowed_group_ids=[9])
        self._entries = [mine, theirs, open_shared, my_group, other_group]
        visible = ai_workflows_keystore_visible_entries(_USER_ID)
        self.assertEqual([1, 3, 4], [e.id for e in visible])

    def test_unknown_entry_should_raise(self):
        with self.assertRaises(KeystoreError):
            ai_workflows_keystore_resolver(_USER_ID).get('NOPE')

    def test_non_string_name_should_raise(self):
        with self.assertRaises(KeystoreError):
            ai_workflows_keystore_resolver(_USER_ID).get(None)

    def test_undecryptable_secret_should_raise(self):
        entry = _entry(1, 'TOKEN', 'v')
        entry.value = 'not-a-fernet-token'
        self._entries = [entry]
        with self.assertRaises(KeystoreError):
            ai_workflows_keystore_resolver(_USER_ID).get('TOKEN')

    def test_non_secret_entry_should_return_its_plain_value(self):
        self._entries = [_entry(1, 'TENANT', 'tenant-42', is_secret=False)]
        self.assertEqual('tenant-42', ai_workflows_keystore_resolver(_USER_ID).get('TENANT'))

    def test_get_should_record_use_and_touch_the_entry_once(self):
        self._entries = [_entry(1, 'TOKEN', 'value')]
        resolver = ai_workflows_keystore_resolver(_USER_ID)
        resolver.get('TOKEN')
        resolver.get('TOKEN')
        self.assertEqual({'TOKEN'}, resolver.used)
        self.assertEqual([1], self._touched)
        self.assertEqual('now', self._entries[0].last_used_at)

    def test_used_should_be_a_copy(self):
        resolver = ai_workflows_keystore_resolver(_USER_ID)
        resolver.used.add('X')
        self.assertEqual(set(), resolver.used)


class TestKeystoreRevealForLlm(_KeystoreTestCase):

    def test_secret_should_be_a_placeholder(self):
        self._entries = [_entry(1, 'TOKEN', 'super-secret')]
        resolver = ai_workflows_keystore_resolver(_USER_ID)
        self.assertEqual('[secret:TOKEN]', resolver.reveal_for_llm('TOKEN'))
        self.assertEqual(set(), resolver.used)
        self.assertEqual([], self._touched)

    def test_non_secret_should_be_the_literal_value(self):
        self._entries = [_entry(1, 'TENANT', 'tenant-42', is_secret=False)]
        resolver = ai_workflows_keystore_resolver(_USER_ID)
        self.assertEqual('tenant-42', resolver.reveal_for_llm('TENANT'))
        self.assertEqual({'TENANT'}, resolver.used)

    def test_unknown_entry_should_raise(self):
        with self.assertRaises(KeystoreError):
            ai_workflows_keystore_resolver(_USER_ID).reveal_for_llm('NOPE')


class TestKeystoreMask(_KeystoreTestCase):

    def _resolver(self):
        self._entries = [
            _entry(1, 'TOKEN', 'tok-123456'),
            _entry(2, 'SHORT', 'abc'),
            _entry(3, 'PLAIN', 'tenant-42', is_secret=False),
            _entry(4, 'LONG', 'tok-123456-extended'),
        ]
        resolver = ai_workflows_keystore_resolver(_USER_ID)
        for name in ('TOKEN', 'SHORT', 'PLAIN', 'LONG'):
            resolver.get(name)
        return resolver

    def test_should_mask_strings(self):
        self.assertEqual(f'Bearer {MASK}', self._resolver().mask('Bearer tok-123456'))

    def test_should_mask_nested_structures(self):
        obj = {
            'headers': {'Authorization': 'Bearer tok-123456'},
            'list': ['tok-123456', ('x', 'tok-123456'), 3, None],
            'tok-123456': True,
        }
        masked = self._resolver().mask(obj)
        self.assertEqual({
            'headers': {'Authorization': f'Bearer {MASK}'},
            'list': [MASK, ('x', MASK), 3, None],
            MASK: True,
        }, masked)

    def test_should_mask_longest_secret_first(self):
        self.assertEqual(f'u={MASK}', self._resolver().mask('u=tok-123456-extended'))

    def test_should_not_mask_short_secrets(self):
        self.assertEqual('abc', self._resolver().mask('abc'))

    def test_should_not_mask_non_secret_values(self):
        self.assertEqual('tenant-42', self._resolver().mask('tenant-42'))

    def test_should_not_mask_unused_secrets(self):
        self._entries = [_entry(1, 'TOKEN', 'tok-123456')]
        resolver = ai_workflows_keystore_resolver(_USER_ID)
        self.assertEqual('tok-123456', resolver.mask('tok-123456'))

    def test_should_not_change_the_original(self):
        original = {'a': ['tok-123456']}
        self._resolver().mask(original)
        self.assertEqual({'a': ['tok-123456']}, original)

    def test_should_mask_encoded_variants(self):
        resolver = self._resolver()
        secret = b'tok-123456'
        for encoded in (base64.b64encode(secret).decode(), base64.b64encode(secret).decode().rstrip('='),
                        base64.urlsafe_b64encode(secret).decode(), secret.hex(), secret.hex().upper(),
                        quote('tok-123456', safe='')):
            self.assertNotIn(encoded, resolver.mask(f'x {encoded} y'), encoded)

    def test_should_mask_secret_inside_basic_auth_header(self):
        resolver = self._resolver()
        for user in ('u', 'us', 'use'):
            header = base64.b64encode(f'{user}:tok-123456'.encode()).decode()
            self.assertIn(MASK, resolver.mask(f'Basic {header}'), user)

    def test_placeholder_secret_should_be_masked_but_not_used(self):
        self._entries = [_entry(1, 'TOKEN', 'tok-123456')]
        resolver = ai_workflows_keystore_resolver(_USER_ID)
        self.assertEqual('[secret:TOKEN]', resolver.reveal_for_llm('TOKEN'))
        self.assertEqual(f'echo {MASK}', resolver.mask('echo tok-123456'))
        self.assertEqual(set(), resolver.used)
        self.assertEqual([], self._touched)


class TestKeystoreRestore(_KeystoreTestCase):

    def test_restore_should_arm_masking_without_touching(self):
        self._entries = [_entry(1, 'TOKEN', 'tok-123456')]
        resolver = ai_workflows_keystore_resolver(_USER_ID)
        resolver.restore(['TOKEN'])
        self.assertEqual(MASK, resolver.mask('tok-123456'))
        self.assertEqual({'TOKEN'}, resolver.used)
        self.assertEqual([], self._touched)

    def test_restore_should_keep_host_restrictions(self):
        self._entries = [_entry(1, 'TOKEN', 'tok-123456', allowed_hosts=['api.example.com'])]
        resolver = ai_workflows_keystore_resolver(_USER_ID)
        resolver.restore(['TOKEN'])
        with self.assertRaises(KeystoreError):
            resolver.check_url('https://evil.example.org/')

    def test_lost_entry_should_refuse_every_host(self):
        resolver = ai_workflows_keystore_resolver(_USER_ID)
        resolver.restore(['GONE'])
        with self.assertRaises(KeystoreError):
            resolver.check_url('https://api.example.com/')


class TestKeystoreCheckUrl(_KeystoreTestCase):

    def _resolver(self, allowed_hosts):
        self._entries = [_entry(1, 'TOKEN', 'tok-123456', allowed_hosts=allowed_hosts)]
        resolver = ai_workflows_keystore_resolver(_USER_ID)
        resolver.get('TOKEN')
        return resolver

    def test_should_accept_any_host_without_allowlist(self):
        self._resolver([]).check_url('https://anything.example.net/x')

    def test_should_accept_exact_host(self):
        self._resolver(['api.example.com']).check_url('https://API.example.com:8443/v1?q=1')

    def test_should_refuse_other_host(self):
        with self.assertRaises(KeystoreError):
            self._resolver(['api.example.com']).check_url('https://evil.example.org/v1')

    def test_should_refuse_lookalike_host(self):
        with self.assertRaises(KeystoreError):
            self._resolver(['api.example.com']).check_url('https://api.example.com.evil.org/')

    def test_should_refuse_userinfo_trick(self):
        with self.assertRaises(KeystoreError):
            self._resolver(['api.example.com']).check_url('https://api.example.com@evil.org/')

    def test_should_accept_wildcard_subdomains(self):
        resolver = self._resolver(['*.example.com'])
        resolver.check_url('https://a.example.com/')
        resolver.check_url('https://a.b.example.com/')

    def test_wildcard_should_not_match_the_apex_or_suffix_lookalikes(self):
        resolver = self._resolver(['*.example.com'])
        with self.assertRaises(KeystoreError):
            resolver.check_url('https://example.com/')
        with self.assertRaises(KeystoreError):
            resolver.check_url('https://badexample.com/')

    def test_should_refuse_url_without_host(self):
        with self.assertRaises(KeystoreError):
            self._resolver(['api.example.com']).check_url('not a url')

    def test_should_ignore_unused_entries(self):
        self._entries = [_entry(1, 'TOKEN', 'tok-123456', allowed_hosts=['api.example.com'])]
        ai_workflows_keystore_resolver(_USER_ID).check_url('https://evil.example.org/')

    def test_host_matches(self):
        self.assertTrue(ai_workflows_keystore_host_matches('a.example.com.', '*.example.com'))
        self.assertTrue(ai_workflows_keystore_host_matches('example.com', 'Example.COM'))
        self.assertFalse(ai_workflows_keystore_host_matches('', 'example.com'))
        self.assertFalse(ai_workflows_keystore_host_matches('example.com', ''))
