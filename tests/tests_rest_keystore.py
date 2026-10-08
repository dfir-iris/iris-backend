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

from unittest import TestCase

from iris import Iris

_IDENTIFIER_FOR_NONEXISTENT_OBJECT = 123456789
_KEYSTORE = '/api/v2/keystore'
_AI_WORKFLOWS_READ = 0x80000000
_AI_WORKFLOWS_WRITE = 0x100000000
_SECRET = 'very-secret-token-value'


def _body(**overrides):
    body = {
        'name': 'API_TOKEN',
        'value': _SECRET,
        'is_secret': True,
        'description': 'Token for the ticketing system',
        'allowed_hosts': ['api.example.com', '*.example.org'],
    }
    body.update(overrides)
    return body


class TestsRestKeystore(TestCase):

    def setUp(self) -> None:
        self._subject = Iris()

    def tearDown(self):
        entries = self._subject.get(_KEYSTORE).json()
        if isinstance(entries, list):
            for entry in entries:
                self._subject.delete(f"{_KEYSTORE}/{entry['id']}")
        self._subject.clear_database()

    def _writer(self):
        return self._subject.create_dummy_user(permissions=_AI_WORKFLOWS_READ | _AI_WORKFLOWS_WRITE)

    def _user_in_group(self, group_identifier):
        user = self._subject.create_dummy_user()
        body = {'groups_membership': [group_identifier]}
        self._subject.create(f'/manage/users/{user.get_identifier()}/groups/update', body)
        return user

    def test_create_entry_should_return_201(self):
        response = self._subject.create(_KEYSTORE, _body())
        self.assertEqual(201, response.status_code)

    def test_create_entry_should_never_return_the_secret_value(self):
        response = self._subject.create(_KEYSTORE, _body())
        self.assertNotIn(_SECRET, response.text)
        body = response.json()
        self.assertIsNone(body['value'])
        self.assertTrue(body['has_value'])
        self.assertEqual('personal', body['scope'])
        self.assertEqual(['api.example.com', '*.example.org'], body['allowed_hosts'])

    def test_list_entries_should_never_return_the_secret_value(self):
        self._subject.create(_KEYSTORE, _body())
        response = self._subject.get(_KEYSTORE)
        self.assertEqual(200, response.status_code)
        self.assertNotIn(_SECRET, response.text)

    def test_create_non_secret_entry_should_return_its_value(self):
        body = self._subject.create(_KEYSTORE, _body(name='TENANT', value='tenant-42', is_secret=False)).json()
        self.assertEqual('tenant-42', body['value'])

    def test_create_entry_should_return_400_for_an_invalid_name(self):
        response = self._subject.create(_KEYSTORE, _body(name='api-token'))
        self.assertEqual(400, response.status_code)
        self.assertIn('name', response.json()['data'])

    def test_create_entry_should_return_400_for_a_value_over_8_kib(self):
        response = self._subject.create(_KEYSTORE, _body(value='x' * 8193))
        self.assertEqual(400, response.status_code)

    def test_create_entry_should_return_400_for_an_invalid_host(self):
        response = self._subject.create(_KEYSTORE, _body(allowed_hosts=['https://api.example.com/']))
        self.assertEqual(400, response.status_code)

    def test_create_entry_should_return_400_for_a_duplicate_name(self):
        self._subject.create(_KEYSTORE, _body())
        response = self._subject.create(_KEYSTORE, _body())
        self.assertEqual(400, response.status_code)

    def test_create_shared_entry_should_return_400_for_an_unknown_group(self):
        response = self._subject.create(_KEYSTORE, _body(scope='shared',
                                                         allowed_group_ids=[_IDENTIFIER_FOR_NONEXISTENT_OBJECT]))
        self.assertEqual(400, response.status_code)

    def test_create_shared_entry_should_return_400_without_allowed_hosts(self):
        response = self._subject.create(_KEYSTORE, _body(scope='shared', allowed_hosts=[]))
        self.assertEqual(400, response.status_code)

    def test_create_personal_entry_without_allowed_hosts_should_return_201(self):
        response = self._subject.create(_KEYSTORE, _body(allowed_hosts=[]))
        self.assertEqual(201, response.status_code)
        self.assertEqual([], response.json()['allowed_hosts'])

    def test_update_shared_entry_should_return_400_when_emptying_its_hosts(self):
        identifier = self._subject.create(_KEYSTORE, _body(scope='shared')).json()['id']
        response = self._subject.update(f'{_KEYSTORE}/{identifier}', {'allowed_hosts': []})
        self.assertEqual(400, response.status_code)

    def test_update_entry_without_value_should_keep_it(self):
        identifier = self._subject.create(_KEYSTORE, _body()).json()['id']
        response = self._subject.update(f'{_KEYSTORE}/{identifier}', {'description': 'changed'})
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertTrue(body['has_value'])
        self.assertEqual('changed', body['description'])

    def test_update_entry_should_return_404_when_it_does_not_exist(self):
        response = self._subject.update(f'{_KEYSTORE}/{_IDENTIFIER_FOR_NONEXISTENT_OBJECT}', {'description': 'x'})
        self.assertEqual(404, response.status_code)

    def test_delete_entry_should_return_204(self):
        identifier = self._subject.create(_KEYSTORE, _body()).json()['id']
        response = self._subject.delete(f'{_KEYSTORE}/{identifier}')
        self.assertEqual(204, response.status_code)

    def test_delete_entry_should_return_404_when_it_does_not_exist(self):
        response = self._subject.delete(f'{_KEYSTORE}/{_IDENTIFIER_FOR_NONEXISTENT_OBJECT}')
        self.assertEqual(404, response.status_code)

    def test_list_entries_should_return_403_without_permission(self):
        user = self._subject.create_dummy_user()
        response = user.get(_KEYSTORE)
        self.assertEqual(403, response.status_code)

    def test_create_entry_should_return_403_with_read_permission_only(self):
        user = self._subject.create_dummy_user(permissions=_AI_WORKFLOWS_READ)
        response = user.create(_KEYSTORE, _body())
        self.assertEqual(403, response.status_code)

    def test_writer_should_create_a_personal_entry(self):
        user = self._writer()
        response = user.create(_KEYSTORE, _body())
        self.assertEqual(201, response.status_code)
        self.assertEqual(user.get_identifier(), response.json()['owner']['id'])

    def test_writer_should_not_create_a_shared_entry(self):
        user = self._writer()
        response = user.create(_KEYSTORE, _body(scope='shared'))
        self.assertEqual(403, response.status_code)

    def test_personal_entries_should_be_invisible_to_other_users(self):
        alice = self._writer()
        bob = self._writer()
        identifier = alice.create(_KEYSTORE, _body()).json()['id']
        self.assertNotIn(identifier, [entry['id'] for entry in bob.get(_KEYSTORE).json()])
        self.assertEqual(404, bob.update(f'{_KEYSTORE}/{identifier}', {'value': 'stolen'}).status_code)
        self.assertEqual(404, bob.delete(f'{_KEYSTORE}/{identifier}').status_code)

    def test_two_users_may_use_the_same_personal_name(self):
        alice = self._writer()
        bob = self._writer()
        alice.create(_KEYSTORE, _body())
        response = bob.create(_KEYSTORE, _body())
        self.assertEqual(201, response.status_code)

    def test_administrator_should_list_personal_entries_of_others_without_values(self):
        alice = self._writer()
        identifier = alice.create(_KEYSTORE, _body()).json()['id']
        response = self._subject.get(_KEYSTORE)
        self.assertIn(identifier, [entry['id'] for entry in response.json()])
        self.assertNotIn(_SECRET, response.text)

    def test_administrator_should_not_edit_personal_entries_of_others(self):
        alice = self._writer()
        identifier = alice.create(_KEYSTORE, _body()).json()['id']
        response = self._subject.update(f'{_KEYSTORE}/{identifier}', {'value': 'replaced'})
        self.assertEqual(403, response.status_code)

    def test_shared_entry_should_be_visible_to_members_of_its_groups_only(self):
        group_identifier = self._subject.create_dummy_group(_AI_WORKFLOWS_READ)
        member = self._user_in_group(group_identifier)
        outsider = self._writer()
        identifier = self._subject.create(_KEYSTORE, _body(scope='shared',
                                                           allowed_group_ids=[group_identifier])).json()['id']
        self.assertIn(identifier, [entry['id'] for entry in member.get(_KEYSTORE).json()])
        self.assertNotIn(identifier, [entry['id'] for entry in outsider.get(_KEYSTORE).json()])

    def test_shared_entry_should_not_be_editable_by_non_administrators(self):
        user = self._writer()
        identifier = self._subject.create(_KEYSTORE, _body(scope='shared')).json()['id']
        response = user.update(f'{_KEYSTORE}/{identifier}', {'value': 'replaced'})
        self.assertEqual(403, response.status_code)
