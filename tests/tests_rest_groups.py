#  IRIS Source Code
#  Copyright (C) 2025 - DFIR-IRIS
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


class TestsRestGroups(TestCase):

    def setUp(self) -> None:
        self._subject = Iris()

    def tearDown(self):
        self._subject.clear_database()

    def test_create_group_should_return_201(self):
        body = {'group_name': 'name', 'group_description': 'description'}
        response = self._subject.create('/api/v2/manage/groups', body)
        self.assertEqual(201, response.status_code)

    def test_create_group_should_set_group_name(self):
        body = {'group_name': 'name', 'group_description': 'description'}
        response = self._subject.create('/api/v2/manage/groups', body).json()
        self.assertEqual('name', response['group_name'])

    def test_create_group_should_return_valid_group_uuid(self):
        body = {'group_name': 'name', 'group_description': 'description'}
        response = self._subject.create('/api/v2/manage/groups', body).json()
        self.assertIsNotNone(response['group_uuid'])

    def test_create_group_should_return_400_when_field_group_description_is_missing(self):
        body = {'group_name': 'name'}
        response = self._subject.create('/api/v2/manage/groups', body)
        self.assertEqual(400, response.status_code)

    def test_create_group_should_return_400_when_field_group_description_is_not_a_string(self):
        body = {'group_name': 'name', 'group_description': 1}
        response = self._subject.create('/api/v2/manage/groups', body)
        self.assertEqual(400, response.status_code)

    def test_create_group_should_return_400_when_a_group_with_the_same_name_already_exists(self):
        body = {'group_name': 'name', 'group_description': 'description'}
        self._subject.create('/api/v2/manage/groups', body)
        response = self._subject.create('/api/v2/manage/groups', body)
        self.assertEqual(400, response.status_code)

    def test_create_event_should_return_403_when_user_has_no_insufficient_permissions(self):
        user = self._subject.create_dummy_user()
        body = {'group_name': 'name', 'group_description': 'description'}
        response = user.create('/api/v2/manage/groups', body)
        self.assertEqual(403, response.status_code)

    def test_get_group_should_return_200(self):
        body = {'group_name': 'name', 'group_description': 'description'}
        response = self._subject.create('/api/v2/manage/groups', body).json()
        identifier = response['group_id']
        response = self._subject.get(f'/api/v2/manage/groups/{identifier}')
        self.assertEqual(200, response.status_code)
