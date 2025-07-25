#  IRIS Source Code
#  Copyright (C) 2023 - DFIR-IRIS
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


class TestsRestProfile(TestCase):

    def setUp(self) -> None:
        self._subject = Iris()

    def tearDown(self):
        self._subject.clear_database()

    def test_update_me_should_return_200(self):
        response = self._subject.update('/api/v2/me', {})
        self.assertEqual(200, response.status_code)

    def test_update_me_should_modify_name(self):
        user = self._subject.create_user('name', 'aA.1234567890')

        response = user.update('/api/v2/me', {'user_name': 'new name'}).json()
        self.assertEqual('new name', response['user_name'])

    def test_update_me_should_modify_email(self):
        user = self._subject.create_dummy_user()

        response = user.update('/api/v2/me', {'user_email': 'new@aa.eu'}).json()
        self.assertEqual('new@aa.eu', response['user_email'])

    def test_update_me_should_modify_password(self):
        user = self._subject.create_user('name', 'aA.1234567890')
        user.update('/api/v2/me', {'user_password': 'bB.1234567890'})
        response = user.login('bB.1234567890')
        self.assertEqual(200, response.status_code)

    def test_update_me_should_modify_ctx_case(self):
        user = self._subject.create_dummy_user()
        case_identifier = self._subject.create_dummy_case()
        response = user.update('/api/v2/me', {'ctx_case': case_identifier}).json()
        self.assertEqual(case_identifier, response['ctx_case'])

    def test_update_me_should_modify_in_dark_mode(self):
        user = self._subject.create_dummy_user()

        response = user.update('/api/v2/me', {'in_dark_mode': True}).json()
        self.assertTrue(response['in_dark_mode'])
