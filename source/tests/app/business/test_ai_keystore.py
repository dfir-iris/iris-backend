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

"""Validation, permission rules and serialization of keystore writes.

The query helpers and `track_activity` are patched; entries are
transient model instances, nothing touches the database.
"""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from app.business.ai_keystore import AiKeystoreForbiddenError
from app.business.ai_keystore import ai_keystore_create
from app.business.ai_keystore import ai_keystore_delete
from app.business.ai_keystore import ai_keystore_is_valid_host
from app.business.ai_keystore import ai_keystore_is_valid_name
from app.business.ai_keystore import ai_keystore_list
from app.business.ai_keystore import ai_keystore_serialize
from app.business.ai_keystore import ai_keystore_update
from app.iris_engine.mail.secrets import decrypt_secret
from app.iris_engine.mail.secrets import encrypt_secret
from app.models.ai_workflows import AiKeystoreEntry
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError

_BUSINESS = 'app.business.ai_keystore'
_USER_ID = 7
_OTHER_USER_ID = 8
_ADMIN_ID = 1


def _stored(entry_id=10, name='TOKEN', value='stored-secret', is_secret=True, scope='personal',
            owner_id=_USER_ID, allowed_group_ids=None, allowed_hosts=None):
    entry = AiKeystoreEntry(
        name=name,
        value=encrypt_secret(value) if is_secret else value,
        is_secret=is_secret,
        scope=scope,
        owner_id=owner_id if scope == 'personal' else None,
        allowed_group_ids=allowed_group_ids or [],
        allowed_hosts=allowed_hosts or [],
    )
    entry.id = entry_id
    return entry


class _KeystoreBusinessTestCase(TestCase):

    def setUp(self):
        self._by_id = {}
        self._existing_groups = [1, 2, 3]
        self._added = []
        self._deleted = []
        self._activities = []
        self._visible = None
        patchers = [
            patch(f'{_BUSINESS}.ai_workflows_db_keystore_get', side_effect=lambda i: self._by_id.get(i)),
            patch(f'{_BUSINESS}.ai_workflows_db_keystore_by_name',
                  side_effect=lambda n: [e for e in self._by_id.values() if e.name == n]),
            patch(f'{_BUSINESS}.ai_workflows_db_keystore_list_all', side_effect=lambda: list(self._by_id.values())),
            patch(f'{_BUSINESS}.ai_workflows_db_existing_group_ids',
                  side_effect=lambda ids: [i for i in ids if i in self._existing_groups]),
            patch(f'{_BUSINESS}.ai_workflows_db_add', side_effect=self._add),
            patch(f'{_BUSINESS}.ai_workflows_db_commit'),
            patch(f'{_BUSINESS}.ai_workflows_db_delete', side_effect=self._deleted.append),
            patch(f'{_BUSINESS}.ai_workflows_keystore_visible_entries', side_effect=self._visible_entries),
            patch(f'{_BUSINESS}.track_activity', side_effect=lambda message, **_kw: self._activities.append(message)),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def _add(self, entry):
        entry.id = 100 + len(self._added)
        self._added.append(entry)
        return entry

    def _visible_entries(self, user_id):
        if self._visible is not None:
            return self._visible
        return [e for e in self._by_id.values()
                if (e.scope == 'personal' and e.owner_id == user_id) or e.scope == 'shared']

    def _store(self, entry):
        self._by_id[entry.id] = entry
        return entry


class TestAiKeystoreNames(TestCase):

    def test_valid_names(self):
        for name in ('A', 'API_TOKEN', 'X1_2', 'A' * 64):
            self.assertTrue(ai_keystore_is_valid_name(name), name)

    def test_invalid_names(self):
        for name in ('', 'a', 'api_token', 'API-TOKEN', 'API TOKEN', 'A' * 65, None, 12, 'É'):
            self.assertFalse(ai_keystore_is_valid_name(name), name)

    def test_valid_hosts(self):
        for host in ('api.example.com', 'localhost', '*.example.com', 'API.Example.com.'):
            self.assertTrue(ai_keystore_is_valid_host(host), host)

    def test_invalid_hosts(self):
        for host in ('', 'https://api.example.com', 'api.example.com/x', 'a..b', '-a.com', 'a b', '*', '*.', 3,
                     'api.example.com:443', 'a.*.com'):
            self.assertFalse(ai_keystore_is_valid_host(host), host)


class TestAiKeystoreCreate(_KeystoreBusinessTestCase):

    def test_should_create_personal_secret_encrypted(self):
        result = ai_keystore_create({'name': 'TOKEN', 'value': 's3cr3t-value'}, _USER_ID, can_write=True)
        entry = self._added[0]
        self.assertEqual('personal', entry.scope)
        self.assertEqual(_USER_ID, entry.owner_id)
        self.assertTrue(entry.is_secret)
        self.assertNotEqual('s3cr3t-value', entry.value)
        self.assertEqual('s3cr3t-value', decrypt_secret(entry.value))
        self.assertIsNone(result['value'])
        self.assertTrue(result['has_value'])

    def test_should_store_non_secret_in_clear_and_return_it(self):
        result = ai_keystore_create({'name': 'TENANT', 'value': 't-42', 'is_secret': False}, _USER_ID, can_write=True)
        self.assertEqual('t-42', self._added[0].value)
        self.assertEqual('t-42', result['value'])

    def test_should_track_activity_without_the_value(self):
        ai_keystore_create({'name': 'TOKEN', 'value': 's3cr3t-value'}, _USER_ID, can_write=True)
        self.assertEqual(1, len(self._activities))
        self.assertIn('TOKEN', self._activities[0])
        self.assertNotIn('s3cr3t-value', self._activities[0])

    def test_personal_should_need_write_permission(self):
        with self.assertRaises(AiKeystoreForbiddenError):
            ai_keystore_create({'name': 'TOKEN', 'value': 'v'}, _USER_ID, can_write=False)

    def test_admin_should_create_personal_without_write_bit(self):
        ai_keystore_create({'name': 'TOKEN', 'value': 'v'}, _ADMIN_ID, is_admin=True)
        self.assertEqual(_ADMIN_ID, self._added[0].owner_id)

    def test_shared_should_need_administrator(self):
        with self.assertRaises(AiKeystoreForbiddenError):
            ai_keystore_create({'name': 'TOKEN', 'value': 'v', 'scope': 'shared'}, _USER_ID, can_write=True)

    def test_admin_should_create_shared_without_owner(self):
        ai_keystore_create({'name': 'TOKEN', 'value': 'v', 'scope': 'shared', 'allowed_group_ids': [2, 1, 2],
                            'allowed_hosts': ['api.example.com']},
                           _ADMIN_ID, is_admin=True)
        entry = self._added[0]
        self.assertIsNone(entry.owner_id)
        self.assertEqual([1, 2], entry.allowed_group_ids)

    def test_should_refuse_invalid_name(self):
        with self.assertRaises(BusinessProcessingError) as context:
            ai_keystore_create({'name': 'bad-name', 'value': 'v'}, _USER_ID, can_write=True)
        self.assertIn('name', context.exception.get_data())

    def test_should_refuse_missing_value(self):
        with self.assertRaises(BusinessProcessingError) as context:
            ai_keystore_create({'name': 'TOKEN'}, _USER_ID, can_write=True)
        self.assertIn('value', context.exception.get_data())

    def test_should_refuse_values_over_8_kib(self):
        with self.assertRaises(BusinessProcessingError) as context:
            ai_keystore_create({'name': 'TOKEN', 'value': 'x' * 8193}, _USER_ID, can_write=True)
        self.assertIn('value', context.exception.get_data())

    def test_should_accept_values_of_8_kib(self):
        ai_keystore_create({'name': 'TOKEN', 'value': 'x' * 8192}, _USER_ID, can_write=True)
        self.assertEqual(1, len(self._added))

    def test_should_refuse_unknown_scope(self):
        with self.assertRaises(BusinessProcessingError) as context:
            ai_keystore_create({'name': 'TOKEN', 'value': 'v', 'scope': 'global'}, _ADMIN_ID, is_admin=True)
        self.assertIn('scope', context.exception.get_data())

    def test_should_refuse_unknown_groups(self):
        with self.assertRaises(BusinessProcessingError) as context:
            ai_keystore_create({'name': 'TOKEN', 'value': 'v', 'scope': 'shared', 'allowed_group_ids': [1, 99]},
                               _ADMIN_ID, is_admin=True)
        self.assertIn('99', context.exception.get_data()['allowed_group_ids'][0])

    def test_should_refuse_groups_on_personal_entries(self):
        with self.assertRaises(BusinessProcessingError) as context:
            ai_keystore_create({'name': 'TOKEN', 'value': 'v', 'allowed_group_ids': [1]}, _USER_ID, can_write=True)
        self.assertIn('allowed_group_ids', context.exception.get_data())

    def test_should_refuse_invalid_hosts(self):
        with self.assertRaises(BusinessProcessingError) as context:
            ai_keystore_create({'name': 'TOKEN', 'value': 'v', 'allowed_hosts': ['https://x.example.com/']},
                               _USER_ID, can_write=True)
        self.assertIn('allowed_hosts', context.exception.get_data())

    def test_should_normalize_hosts(self):
        ai_keystore_create({'name': 'TOKEN', 'value': 'v', 'allowed_hosts': ['API.example.com.', 'api.example.com',
                                                                               '*.Example.org']},
                           _USER_ID, can_write=True)
        self.assertEqual(['api.example.com', '*.example.org'], self._added[0].allowed_hosts)

    def test_personal_names_should_be_unique_per_owner(self):
        self._store(_stored(entry_id=1, name='TOKEN', owner_id=_USER_ID))
        with self.assertRaises(BusinessProcessingError) as context:
            ai_keystore_create({'name': 'TOKEN', 'value': 'v'}, _USER_ID, can_write=True)
        self.assertIn('name', context.exception.get_data())

    def test_personal_names_may_repeat_across_owners_and_scopes(self):
        self._store(_stored(entry_id=1, name='TOKEN', owner_id=_OTHER_USER_ID))
        self._store(_stored(entry_id=2, name='TOKEN', scope='shared'))
        ai_keystore_create({'name': 'TOKEN', 'value': 'v'}, _USER_ID, can_write=True)
        self.assertEqual(1, len(self._added))

    def test_shared_names_should_be_unique(self):
        self._store(_stored(entry_id=1, name='TOKEN', scope='shared'))
        with self.assertRaises(BusinessProcessingError) as context:
            ai_keystore_create({'name': 'TOKEN', 'value': 'v', 'scope': 'shared', 'allowed_hosts': ['a.example.com']},
                               _ADMIN_ID, is_admin=True)
        self.assertIn('name', context.exception.get_data())

    def test_shared_entry_without_hosts_should_be_refused(self):
        for hosts in (None, []):
            body = {'name': 'TOKEN', 'value': 'v', 'scope': 'shared'}
            if hosts is not None:
                body['allowed_hosts'] = hosts
            with self.assertRaises(BusinessProcessingError) as context:
                ai_keystore_create(body, _ADMIN_ID, is_admin=True)
            self.assertIn('allowed_hosts', context.exception.get_data())
        self.assertEqual([], self._added)

    def test_personal_entry_without_hosts_should_be_accepted(self):
        ai_keystore_create({'name': 'TOKEN', 'value': 'v', 'allowed_hosts': []}, _USER_ID, can_write=True)
        self.assertEqual([], self._added[0].allowed_hosts)


class TestAiKeystoreUpdate(_KeystoreBusinessTestCase):

    def test_omitted_value_should_keep_the_secret(self):
        entry = self._store(_stored())
        ciphertext = entry.value
        ai_keystore_update(entry.id, {'description': 'new'}, _USER_ID, can_write=True)
        self.assertEqual(ciphertext, entry.value)
        self.assertEqual('new', entry.description)

    def test_null_or_empty_value_should_keep_the_secret(self):
        entry = self._store(_stored())
        ciphertext = entry.value
        ai_keystore_update(entry.id, {'value': None}, _USER_ID, can_write=True)
        ai_keystore_update(entry.id, {'value': ''}, _USER_ID, can_write=True)
        self.assertEqual(ciphertext, entry.value)

    def test_new_value_should_be_encrypted(self):
        entry = self._store(_stored())
        ai_keystore_update(entry.id, {'value': 'rotated-value'}, _USER_ID, can_write=True)
        self.assertEqual('rotated-value', decrypt_secret(entry.value))

    def test_secret_should_not_become_plain_without_new_value(self):
        entry = self._store(_stored())
        with self.assertRaises(BusinessProcessingError) as context:
            ai_keystore_update(entry.id, {'is_secret': False}, _USER_ID, can_write=True)
        self.assertIn('value', context.exception.get_data())

    def test_plain_entry_becoming_secret_should_be_encrypted(self):
        entry = self._store(_stored(value='plain-value', is_secret=False))
        result = ai_keystore_update(entry.id, {'is_secret': True}, _USER_ID, can_write=True)
        self.assertEqual('plain-value', decrypt_secret(entry.value))
        self.assertIsNone(result['value'])

    def test_scope_should_not_change(self):
        entry = self._store(_stored())
        with self.assertRaises(BusinessProcessingError) as context:
            ai_keystore_update(entry.id, {'scope': 'shared'}, _USER_ID, can_write=True)
        self.assertIn('scope', context.exception.get_data())

    def test_rename_should_not_collide(self):
        self._store(_stored(entry_id=1, name='OTHER'))
        entry = self._store(_stored(entry_id=2, name='TOKEN'))
        with self.assertRaises(BusinessProcessingError):
            ai_keystore_update(entry.id, {'name': 'OTHER'}, _USER_ID, can_write=True)

    def test_keeping_the_same_name_should_not_collide_with_itself(self):
        entry = self._store(_stored())
        ai_keystore_update(entry.id, {'name': 'TOKEN'}, _USER_ID, can_write=True)
        self.assertEqual(1, len(self._activities))

    def test_unknown_entry_should_be_not_found(self):
        with self.assertRaises(ObjectNotFoundError):
            ai_keystore_update(404, {}, _USER_ID, can_write=True)

    def test_other_users_personal_entry_should_be_not_found(self):
        entry = self._store(_stored(owner_id=_OTHER_USER_ID))
        with self.assertRaises(ObjectNotFoundError):
            ai_keystore_update(entry.id, {'value': 'x'}, _USER_ID, can_write=True)

    def test_admin_should_not_edit_other_users_personal_entry(self):
        entry = self._store(_stored(owner_id=_OTHER_USER_ID))
        with self.assertRaises(AiKeystoreForbiddenError):
            ai_keystore_update(entry.id, {'value': 'x'}, _ADMIN_ID, is_admin=True)

    def test_own_personal_entry_should_need_write_permission(self):
        entry = self._store(_stored())
        with self.assertRaises(AiKeystoreForbiddenError):
            ai_keystore_update(entry.id, {'value': 'x'}, _USER_ID, can_write=False)

    def test_non_admin_should_not_edit_visible_shared_entry(self):
        entry = self._store(_stored(scope='shared'))
        with self.assertRaises(AiKeystoreForbiddenError):
            ai_keystore_update(entry.id, {'value': 'x'}, _USER_ID, can_write=True)

    def test_non_admin_should_not_find_invisible_shared_entry(self):
        entry = self._store(_stored(scope='shared', allowed_group_ids=[3]))
        self._visible = []
        with self.assertRaises(ObjectNotFoundError):
            ai_keystore_update(entry.id, {'value': 'x'}, _USER_ID, can_write=True)

    def test_admin_should_edit_shared_entry(self):
        entry = self._store(_stored(scope='shared'))
        ai_keystore_update(entry.id, {'allowed_hosts': ['*.example.com']}, _ADMIN_ID, is_admin=True)
        self.assertEqual(['*.example.com'], entry.allowed_hosts)

    def test_shared_entry_hosts_should_not_be_emptied(self):
        entry = self._store(_stored(scope='shared', allowed_hosts=['api.example.com']))
        with self.assertRaises(BusinessProcessingError) as context:
            ai_keystore_update(entry.id, {'allowed_hosts': []}, _ADMIN_ID, is_admin=True)
        self.assertIn('allowed_hosts', context.exception.get_data())
        self.assertEqual(['api.example.com'], entry.allowed_hosts)


class TestAiKeystoreDelete(_KeystoreBusinessTestCase):

    def test_owner_should_delete_personal_entry(self):
        entry = self._store(_stored())
        ai_keystore_delete(entry.id, _USER_ID, can_write=True)
        self.assertEqual([entry], self._deleted)
        self.assertIn('deleted', self._activities[0])

    def test_other_user_should_not_find_personal_entry(self):
        entry = self._store(_stored(owner_id=_OTHER_USER_ID))
        with self.assertRaises(ObjectNotFoundError):
            ai_keystore_delete(entry.id, _USER_ID, can_write=True)
        self.assertEqual([], self._deleted)

    def test_admin_should_delete_any_entry(self):
        entry = self._store(_stored(owner_id=_OTHER_USER_ID))
        ai_keystore_delete(entry.id, _ADMIN_ID, is_admin=True)
        self.assertEqual([entry], self._deleted)

    def test_non_admin_should_not_delete_shared_entry(self):
        entry = self._store(_stored(scope='shared'))
        with self.assertRaises(AiKeystoreForbiddenError):
            ai_keystore_delete(entry.id, _USER_ID, can_write=True)


class TestAiKeystoreList(_KeystoreBusinessTestCase):

    def test_admin_should_list_every_entry_without_secret_values(self):
        self._store(_stored(entry_id=1, owner_id=_OTHER_USER_ID, value='other-secret'))
        self._store(_stored(entry_id=2, scope='shared', value='shared-secret'))
        result = ai_keystore_list(_ADMIN_ID, is_admin=True)
        self.assertEqual([1, 2], [e['id'] for e in result])
        self.assertTrue(all(e['value'] is None and e['has_value'] for e in result))

    def test_user_should_list_visible_entries_only(self):
        self._visible = [_stored(entry_id=3)]
        self._store(_stored(entry_id=4, owner_id=_OTHER_USER_ID))
        self.assertEqual([3], [e['id'] for e in ai_keystore_list(_USER_ID)])


class TestAiKeystoreSerialize(TestCase):

    def test_should_have_the_documented_shape(self):
        entry = _stored(allowed_hosts=['api.example.com'])
        entry.owner = SimpleNamespace(id=_USER_ID, user='jdoe', name='John Doe')
        result = ai_keystore_serialize(entry)
        self.assertEqual({'id', 'name', 'is_secret', 'value', 'has_value', 'description', 'scope', 'owner',
                          'allowed_group_ids', 'allowed_hosts', 'created_at', 'updated_at', 'last_used_at'},
                         set(result))
        self.assertIsNone(result['value'])
        self.assertEqual({'id': _USER_ID, 'login': 'jdoe', 'name': 'John Doe'}, result['owner'])
        self.assertEqual(['api.example.com'], result['allowed_hosts'])

    def test_shared_entry_should_have_no_owner(self):
        self.assertIsNone(ai_keystore_serialize(_stored(scope='shared'))['owner'])
