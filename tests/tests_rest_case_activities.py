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

_IDENTIFIER_FOR_NONEXISTENT_OBJECT = 123456789


class TestsRestCaseActivities(TestCase):

    def setUp(self) -> None:
        self._subject = Iris()

    def tearDown(self):
        self._subject.clear_database()

    def test_list_case_activities_should_return_200(self):
        case_identifier = self._subject.create_dummy_case()
        response = self._subject.get(f'/api/v2/cases/{case_identifier}/activities')
        self.assertEqual(200, response.status_code)

    def test_list_case_activities_should_return_404_when_case_does_not_exist(self):
        response = self._subject.get(f'/api/v2/cases/{_IDENTIFIER_FOR_NONEXISTENT_OBJECT}/activities')
        self.assertEqual(404, response.status_code)

    def test_list_case_activities_should_return_403_when_user_has_no_access_to_case(self):
        case_identifier = self._subject.create_dummy_case()
        user = self._subject.create_dummy_user()
        response = user.get(f'/api/v2/cases/{case_identifier}/activities')
        self.assertEqual(403, response.status_code)

    def test_list_case_activities_should_return_a_list(self):
        case_identifier = self._subject.create_dummy_case()
        response = self._subject.get(f'/api/v2/cases/{case_identifier}/activities').json()
        self.assertIsInstance(response, list)

    def test_list_case_activities_should_include_case_creation_activity(self):
        case_identifier = self._subject.create_dummy_case()
        response = self._subject.get(f'/api/v2/cases/{case_identifier}/activities').json()
        self.assertGreaterEqual(len(response), 1)

    def test_list_case_activities_entries_should_include_user_name(self):
        case_identifier = self._subject.create_dummy_case()
        response = self._subject.get(f'/api/v2/cases/{case_identifier}/activities').json()
        self.assertIn('user_name', response[0])

    def test_list_case_activities_entries_should_include_activity_date(self):
        case_identifier = self._subject.create_dummy_case()
        response = self._subject.get(f'/api/v2/cases/{case_identifier}/activities').json()
        self.assertIn('activity_date', response[0])

    def test_list_case_activities_entries_should_include_activity_description(self):
        case_identifier = self._subject.create_dummy_case()
        response = self._subject.get(f'/api/v2/cases/{case_identifier}/activities').json()
        self.assertIn('activity_desc', response[0])

    def test_create_case_activity_should_return_201(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'log_content': 'Pulled the memory image off the host'}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/activities', body)
        self.assertEqual(201, response.status_code)

    def test_create_case_activity_should_return_the_created_entry(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'log_content': 'Pulled the memory image off the host'}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/activities', body).json()
        self.assertEqual('Pulled the memory image off the host', response['activity_desc'])

    def test_create_case_activity_should_add_the_entry_to_the_case_log(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'log_content': 'Pulled the memory image off the host'}
        self._subject.create(f'/api/v2/cases/{case_identifier}/activities', body)
        response = self._subject.get(f'/api/v2/cases/{case_identifier}/activities').json()
        self.assertIn('Pulled the memory image off the host',
                      [activity['activity_desc'] for activity in response])

    def test_create_case_activity_should_return_400_when_log_content_is_missing(self):
        case_identifier = self._subject.create_dummy_case()
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/activities', {})
        self.assertEqual(400, response.status_code)

    def test_create_case_activity_should_return_400_when_log_content_is_empty(self):
        case_identifier = self._subject.create_dummy_case()
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/activities', {'log_content': ''})
        self.assertEqual(400, response.status_code)

    def test_create_case_activity_should_return_404_when_case_does_not_exist(self):
        body = {'log_content': 'Pulled the memory image off the host'}
        response = self._subject.create(f'/api/v2/cases/{_IDENTIFIER_FOR_NONEXISTENT_OBJECT}/activities', body)
        self.assertEqual(404, response.status_code)

    def test_create_case_activity_should_return_403_when_user_has_no_access_to_case(self):
        case_identifier = self._subject.create_dummy_case()
        user = self._subject.create_dummy_user()
        body = {'log_content': 'Pulled the memory image off the host'}
        response = user.create(f'/api/v2/cases/{case_identifier}/activities', body)
        self.assertEqual(403, response.status_code)
