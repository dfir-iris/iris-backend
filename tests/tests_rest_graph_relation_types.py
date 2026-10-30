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

"""Integration tests for `/api/v2/manage/graph-relation-types`."""

from unittest import TestCase

from iris import Iris

_PATH = '/api/v2/manage/graph-relation-types'


class TestsRestGraphRelationTypes(TestCase):

    def setUp(self) -> None:
        self._subject = Iris()

    def tearDown(self):
        # Cases first: their edges would keep a custom type in use.
        self._subject.clear_database()
        relation_types = self._subject.get(_PATH).json()
        if isinstance(relation_types, list):
            for relation_type in relation_types:
                if not relation_type['is_system']:
                    self._subject.delete(f"{_PATH}/{relation_type['id']}")

    def _create(self, name='controls', user=None, **body):
        return (user or self._subject).create(_PATH, {'name': name, 'label': name.title(), **body})

    def test_list_should_return_the_system_types(self):
        response = self._subject.get(_PATH)
        self.assertEqual(200, response.status_code)
        rows = {row['name']: row for row in response.json()}
        self.assertTrue({'related_to', 'co_occurs', 'lateral_movement', 'same_as'} <= set(rows))
        self.assertTrue(rows['related_to']['is_system'])
        self.assertIn('edge_count', rows['related_to'])

    def test_list_should_be_readable_by_any_user(self):
        user = self._subject.create_dummy_user()
        self.assertEqual(200, user.get(_PATH).status_code)

    def test_create_should_return_201(self):
        response = self._create(directed=True, color='red', line_style='dashed', stix_relationship='controls')
        self.assertEqual(201, response.status_code)
        body = response.json()
        self.assertEqual(('controls', True, 'red', 'dashed', False),
                         (body['name'], body['directed'], body['color'], body['line_style'], body['is_system']))

    def test_create_should_require_server_administrator(self):
        user = self._subject.create_dummy_user()
        self.assertEqual(403, self._create(user=user).status_code)

    def test_create_should_reject_a_duplicate_or_invalid_name(self):
        self._create()
        self.assertEqual(400, self._create().status_code)
        self.assertEqual(400, self._create('Bad Name').status_code)
        self.assertEqual(400, self._create('ok_name', color='plaid').status_code)

    def test_update_should_return_200(self):
        identifier = self._create().json()['id']
        response = self._subject.update(f'{_PATH}/{identifier}', {'label': 'Controls (C2)'})
        self.assertEqual(200, response.status_code)
        self.assertEqual('Controls (C2)', response.json()['label'])

    def test_update_should_keep_the_name_of_a_system_type(self):
        rows = {row['name']: row for row in self._subject.get(_PATH).json()}
        response = self._subject.update(f"{_PATH}/{rows['related_to']['id']}", {'name': 'renamed'})
        self.assertEqual(400, response.status_code)

    def test_delete_should_return_204(self):
        identifier = self._create().json()['id']
        self.assertEqual(204, self._subject.delete(f'{_PATH}/{identifier}').status_code)
        self.assertEqual(404, self._subject.delete(f'{_PATH}/{identifier}').status_code)

    def test_delete_should_refuse_a_system_type(self):
        rows = {row['name']: row for row in self._subject.get(_PATH).json()}
        self.assertEqual(400, self._subject.delete(f"{_PATH}/{rows['related_to']['id']}").status_code)

    def test_delete_should_refuse_a_type_in_use(self):
        identifier = self._create().json()['id']
        case_id = self._subject.create_dummy_case()
        assets = [self._subject.create(f'/api/v2/cases/{case_id}/assets',
                                       {'asset_type_id': 1, 'asset_name': name}).json()['asset_id']
                  for name in ('A', 'B')]
        self._subject.create(f'/api/v2/cases/{case_id}/graph/edges',
                             {'source': f'asset:{assets[0]}', 'target': f'asset:{assets[1]}', 'relation': 'controls'})
        self.assertEqual(400, self._subject.delete(f'{_PATH}/{identifier}').status_code)

    def test_reorder(self):
        identifier = self._create().json()['id']
        ids = [row['id'] for row in self._subject.get(_PATH).json()]
        ids.remove(identifier)
        ids.insert(0, identifier)
        response = self._subject.create(f'{_PATH}/reorder', {'ids': ids})
        self.assertEqual(200, response.status_code)
        self.assertEqual(ids, [row['id'] for row in self._subject.get(_PATH).json()])
        self.assertEqual(400, self._subject.create(f'{_PATH}/reorder', {'ids': ids[1:]}).status_code)

    def test_reorder_should_require_server_administrator(self):
        user = self._subject.create_dummy_user()
        self.assertEqual(403, user.create(f'{_PATH}/reorder', {'ids': []}).status_code)
