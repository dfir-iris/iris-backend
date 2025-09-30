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

_IDENTIFIER_FOR_NONEXISTENT_OBJECT = 123456789


class TestsRestNotesDirectories(TestCase):

    def setUp(self) -> None:
        self._subject = Iris()

    def tearDown(self):
        self._subject.clear_database()

    def test_create_note_directory_should_return_201(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'name': 'directory_name'}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/notes-directories', body)
        self.assertEqual(201, response.status_code)

    def test_create_note_directory_should_set_name(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'name': 'directory_name'}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/notes-directories', body).json()
        self.assertEqual('directory_name', response['name'])

    def test_create_note_directory_should_ignore_field_id_when_set(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'name': 'directory_name', 'id': 124}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/notes-directories', body)
        self.assertEqual(201, response.status_code)

    def test_create_note_directory_should_return_400_when_field_name_is_missing(self):
        case_identifier = self._subject.create_dummy_case()
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/notes-directories', {})
        self.assertEqual(400, response.status_code)

    def test_create_note_directory_should_return_400_when_field_type_is_incorrect(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'name': 10}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/notes-directories', body)
        self.assertEqual(400, response.status_code)

    def test_create_note_directory_should_return_400_when_parent_id_does_not_exist(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'name': 'directory_name', 'parent_id': _IDENTIFIER_FOR_NONEXISTENT_OBJECT}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/notes-directories', body)
        self.assertEqual(400, response.status_code)

    def test_create_note_directory_should_return_404_when_case_does_not_exist(self):
        body = {'name': 'directory_name'}
        response = self._subject.create(f'/api/v2/cases/{_IDENTIFIER_FOR_NONEXISTENT_OBJECT}/notes-directories', body)
        self.assertEqual(404, response.status_code)

    def test_create_note_directory_should_return_403_when_user_has_no_access_to_case(self):
        case_identifier = self._subject.create_dummy_case()

        user = self._subject.create_dummy_user()
        body = {'name': 'directory_name'}
        response = user.create(f'/api/v2/cases/{case_identifier}/notes-directories', body)
        self.assertEqual(403, response.status_code)

    def test_get_note_directory_should_return_200(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'name': 'directory_name'}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/notes-directories', body).json()
        identifier = response['id']

        response = self._subject.get(f'/api/v2/cases/{case_identifier}/notes-directories/{identifier}')
        self.assertEqual(200, response.status_code)

    def test_get_note_directory_should_return_field_name(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'name': 'directory_name'}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/notes-directories', body).json()
        identifier = response['id']

        response = self._subject.get(f'/api/v2/cases/{case_identifier}/notes-directories/{identifier}').json()
        self.assertEqual('directory_name', response['name'])

    def test_get_note_directory_should_return_404_when_case_does_not_exist(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'name': 'directory_name'}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/notes-directories', body).json()
        identifier = response['id']

        response = self._subject.get(f'/api/v2/cases/{_IDENTIFIER_FOR_NONEXISTENT_OBJECT}/notes-directories/{identifier}')
        self.assertEqual(404, response.status_code)

    def test_get_note_directory_should_return_404_when_note_directory_does_not_exist(self):
        case_identifier = self._subject.create_dummy_case()
        response = self._subject.get(f'/api/v2/cases/{case_identifier}/notes-directories/{_IDENTIFIER_FOR_NONEXISTENT_OBJECT}')
        self.assertEqual(404, response.status_code)

    def test_get_note_directory_should_return_403_when_user_has_no_permission_to_access_to_case(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'name': 'directory_name'}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/notes-directories', body).json()
        identifier = response['id']

        user = self._subject.create_dummy_user()
        response = user.get(f'/api/v2/cases/{case_identifier}/notes-directories/{identifier}')
        self.assertEqual(403, response.status_code)

    def test_get_note_directory_should_return_404_when_case_identifier_does_not_match_event_case(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'name': 'directory_name'}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/notes-directories', body).json()
        identifier = response['id']

        case_identifier2 = self._subject.create_dummy_case()
        response = self._subject.get(f'/api/v2/cases/{case_identifier2}/notes-directories/{identifier}')
        self.assertEqual(404, response.status_code)

    def test_update_note_directory_should_return_200(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'name': 'directory_name'}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/notes-directories', body).json()
        identifier = response['id']

        response = self._subject.update(f'/api/v2/cases/{case_identifier}/notes-directories/{identifier}', {})
        self.assertEqual(200, response.status_code)

    def test_update_note_directory_should_modify_name(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'name': 'directory_name'}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/notes-directories', body).json()
        identifier = response['id']

        body = {'name': 'new name'}
        response = self._subject.update(f'/api/v2/cases/{case_identifier}/notes-directories/{identifier}', body).json()
        self.assertEqual('new name', response['name'])

    def test_update_note_directory_should_add_an_activity(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'name': 'directory_name'}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/notes-directories', body).json()
        identifier = response['id']

        body = {'name': 'new name'}
        self._subject.update(f'/api/v2/cases/{case_identifier}/notes-directories/{identifier}', body)
        last_activity = self._subject.get_latest_activity()
        self.assertEqual('Modified directory "new name"', last_activity['activity_desc'])

    def test_update_note_directory_should_return_400_when_field_name_is_not_a_string(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'name': 'directory_name'}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/notes-directories', body).json()
        identifier = response['id']

        body = {'name': 123}
        response = self._subject.update(f'/api/v2/cases/{case_identifier}/notes-directories/{identifier}', body)
        self.assertEqual(400, response.status_code)

    def test_update_note_directory_should_return_400_when_field_parent_id_is_the_current_identifier(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'name': 'directory_name'}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/notes-directories', body).json()
        identifier = response['id']

        body = {'parent_id': identifier}
        response = self._subject.update(f'/api/v2/cases/{case_identifier}/notes-directories/{identifier}', body)
        self.assertEqual(400, response.status_code)

    def test_update_note_directory_should_return_404_when_case_identifier_does_not_correspond_to_existing_case(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'name': 'directory_name'}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/notes-directories', body).json()
        identifier = response['id']

        response = self._subject.update(f'/api/v2/cases/{_IDENTIFIER_FOR_NONEXISTENT_OBJECT}/notes-directories/{identifier}', {})
        self.assertEqual(404, response.status_code)

    def test_update_note_directory_should_return_404_when_identifier_does_not_correspond_to_existing_note_directory(self):
        case_identifier = self._subject.create_dummy_case()

        response = self._subject.update(f'/api/v2/cases/{case_identifier}/notes-directories/{_IDENTIFIER_FOR_NONEXISTENT_OBJECT}', {})
        self.assertEqual(404, response.status_code)

    def test_update_note_directory_should_return_403_when_user_has_no_access_to_case(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'name': 'directory_name'}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/notes-directories', body).json()
        identifier = response['id']

        user = self._subject.create_dummy_user()
        response = user.update(f'/api/v2/cases/{case_identifier}/notes-directories/{identifier}', {})
        self.assertEqual(403, response.status_code)

    def test_update_note_directory_should_return_404_when_case_identifier_does_not_match_note_directory_case(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'name': 'directory_name'}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/notes-directories', body).json()
        identifier = response['id']

        case_identifier2 = self._subject.create_dummy_case()
        response = self._subject.update(f'/api/v2/cases/{case_identifier2}/notes-directories/{identifier}', {})
        self.assertEqual(404, response.status_code)

    def test_delete_note_directory_should_return_204(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'name': 'directory_name'}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/notes-directories', body).json()
        identifier = response['id']

        response = self._subject.delete(f'/api/v2/cases/{case_identifier}/notes-directories/{identifier}')
        self.assertEqual(204, response.status_code)

    def test_get_note_directory_should_return_404_after_note_directory_has_been_deleted(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'name': 'directory_name'}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/notes-directories', body).json()
        identifier = response['id']
        self._subject.delete(f'/api/v2/cases/{case_identifier}/notes-directories/{identifier}')

        response = self._subject.get(f'/api/v2/cases/{case_identifier}/notes-directories/{identifier}')
        self.assertEqual(404, response.status_code)

    def test_delete_note_directory_should_return_404_when_case_does_not_exist(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'name': 'directory_name'}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/notes-directories', body).json()
        identifier = response['id']

        response = self._subject.delete(f'/api/v2/cases/{_IDENTIFIER_FOR_NONEXISTENT_OBJECT}/notes-directories/{identifier}')
        self.assertEqual(404, response.status_code)

    def test_delete_note_directory_should_return_403_when_user_has_no_access_to_case(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'name': 'directory_name'}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/notes-directories', body).json()
        identifier = response['id']

        user = self._subject.create_dummy_user()
        response = user.delete(f'/api/v2/cases/{case_identifier}/notes-directories/{identifier}')
        self.assertEqual(403, response.status_code)

    def test_get_notes_directories_filter_should_return_200(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'name': 'directory_name'}
        self._subject.create(f'/api/v2/cases/{case_identifier}/notes-directories', body).json()

        response = self._subject.get(f'/api/v2/cases/{case_identifier}/notes-directories')
        self.assertEqual(200, response.status_code)

    def test_get_notes_directories_should_return_403_when_user_has_no_access_to_case(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'name': 'directory_name'}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/notes-directories', body).json()

        user = self._subject.create_dummy_user()
        response = user.get(f'/api/v2/cases/{case_identifier}/notes-directories')
        self.assertEqual(403, response.status_code)
