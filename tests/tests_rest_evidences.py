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


class TestsRestEvidences(TestCase):

    def setUp(self) -> None:
        self._subject = Iris()

    def tearDown(self):
        self._subject.clear_database()

    def test_create_evidence_should_return_201(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'filename': 'filename'}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/evidences', body)
        self.assertEqual(201, response.status_code)

    def test_create_evidence_should_return_400_when_field_filename_is_missing(self):
        case_identifier = self._subject.create_dummy_case()
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/evidences', {})
        self.assertEqual(400, response.status_code)

    def test_create_evidence_should_return_403_when_user_has_no_permission_to_access_case(self):
        case_identifier = self._subject.create_dummy_case()

        user = self._subject.create_dummy_user()
        response = user.create(f'/api/v2/cases/{case_identifier}/evidences', {'filename': 'filename'})
        self.assertEqual(403, response.status_code)

    def test_create_evidence_should_return_404_when_case_is_missing(self):
        body = {'filename': 'filename'}
        response = self._subject.create(f'/api/v2/cases/{_IDENTIFIER_FOR_NONEXISTENT_OBJECT}/evidences', body)
        self.assertEqual(404, response.status_code)

    def test_create_evidence_should_accept_field_type_id(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'filename': 'filename', 'type_id': 2}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/evidences', body).json()
        self.assertEqual(2, response['type_id'])

    def test_create_evidence_should_accept_field_file_hash(self):
        case_identifier = self._subject.create_dummy_case()
        file_hash = '88BC9EF6F07F0FAE922AB25EB226906542F8BA0DC1A221F3EA7273CBCB5DB0D4'
        body = {'filename': 'filename', 'file_hash': file_hash}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/evidences', body).json()
        self.assertEqual(file_hash, response['file_hash'])

    def test_create_evidence_should_accept_field_file_size(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'filename': 'filename', 'file_size': 77108}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/evidences', body).json()
        self.assertEqual(77108, response['file_size'])

    def test_create_evidence_should_accept_field_start_date(self):
        case_identifier = self._subject.create_dummy_case()
        start_date = '2024-04-13T03:02:00'
        body = {'filename': 'filename', 'start_date': start_date}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/evidences', body).json()
        self.assertEqual(start_date, response['start_date'])

    def test_create_evidence_should_accept_field_file_description(self):
        case_identifier = self._subject.create_dummy_case()
        file_description = 'File description'
        body = {'filename': 'filename', 'file_description': file_description}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/evidences', body).json()
        self.assertEqual(file_description, response['file_description'])

    def test_get_evidence_should_return_200(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'filename': 'filename'}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/evidences', body).json()
        identifier = response['id']
        response = self._subject.get(f'/api/v2/cases/{case_identifier}/evidences/{identifier}')
        self.assertEqual(200, response.status_code)

    def test_get_evidence_should_return_404_when_evidence_does_not_exist(self):
        case_identifier = self._subject.create_dummy_case()
        response = self._subject.get(f'/api/v2/cases/{case_identifier}/evidences/{_IDENTIFIER_FOR_NONEXISTENT_OBJECT}')
        self.assertEqual(404, response.status_code)

    def test_get_evidence_should_return_404_when_case_does_not_exist(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'filename': 'filename'}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/evidences', body).json()
        identifier = response['id']
        response = self._subject.get(f'/api/v2/cases/{_IDENTIFIER_FOR_NONEXISTENT_OBJECT}/evidences/{identifier}')
        self.assertEqual(404, response.status_code)

    def test_get_evidence_should_return_403_when_user_has_no_access_to_case(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'filename': 'filename'}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/evidences', body).json()
        identifier = response['id']

        user = self._subject.create_dummy_user()
        response = user.get(f'/api/v2/cases/{case_identifier}/evidences/{identifier}')
        self.assertEqual(403, response.status_code)

    def test_get_evidence_should_return_400_when_evidence_does_not_belong_to_case(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'filename': 'filename'}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/evidences', body).json()
        identifier = response['id']
        case_identifier2 = self._subject.create_dummy_case()
        response = self._subject.get(f'/api/v2/cases/{case_identifier2}/evidences/{identifier}')
        self.assertEqual(400, response.status_code)

    def test_get_evidences_should_return_200(self):
        case_identifier = self._subject.create_dummy_case()
        response = self._subject.get(f'/api/v2/cases/{case_identifier}/evidences')
        self.assertEqual(200, response.status_code)

    def test_get_evidences_should_return_403_when_user_has_no_access_to_case(self):
        case_identifier = self._subject.create_dummy_case()

        user = self._subject.create_dummy_user()
        response = user.get(f'/api/v2/cases/{case_identifier}/evidences')
        self.assertEqual(403, response.status_code)

    def test_get_evidences_should_return_total(self):
        case_identifier = self._subject.create_dummy_case()
        response = self._subject.get(f'/api/v2/cases/{case_identifier}/evidences').json()
        self.assertEqual(0, response['total'])

    def test_get_evidences_should_order_by_descending_date(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'filename': 'filename1'}
        self._subject.create(f'/api/v2/cases/{case_identifier}/evidences', body).json()
        body = {'filename': 'filename2'}
        self._subject.create(f'/api/v2/cases/{case_identifier}/evidences', body).json()

        response = self._subject.get(f'/api/v2/cases/{case_identifier}/evidences').json()
        self.assertEqual('filename2', response['data'][0]['filename'])

    def test_get_evidences_should_order_by_ascending_date_when_sort_dir_is_set_to_asc(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'filename': 'filename1'}
        self._subject.create(f'/api/v2/cases/{case_identifier}/evidences', body).json()
        body = {'filename': 'filename2'}
        self._subject.create(f'/api/v2/cases/{case_identifier}/evidences', body).json()

        response = self._subject.get(f'/api/v2/cases/{case_identifier}/evidences', {'sort_dir': 'asc'}).json()
        self.assertEqual('filename1', response['data'][0]['filename'])

    def test_get_evidences_should_return_400_when_order_by_is_an_invalid_field(self):
        case_identifier = self._subject.create_dummy_case()
        response = self._subject.get(f'/api/v2/cases/{case_identifier}/evidences', {'order_by': 'an_invalid_field'})
        self.assertEqual(400, response.status_code)

    def test_get_evidences_should_return_404_when_the_case_does_not_exist(self):
        response = self._subject.get(f'/api/v2/cases/{_IDENTIFIER_FOR_NONEXISTENT_OBJECT}/evidences')
        self.assertEqual(404, response.status_code)

    def test_get_evidences_should_accept_per_page_query_parameter(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'filename': 'filename1'}
        self._subject.create(f'/api/v2/cases/{case_identifier}/evidences', body).json()
        body = {'filename': 'filename2'}
        self._subject.create(f'/api/v2/cases/{case_identifier}/evidences', body).json()

        response = self._subject.get(f'/api/v2/cases/{case_identifier}/evidences', {'per_page': 1}).json()
        self.assertEqual(1, len(response['data']))

    def test_update_evidence_should_return_200(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'filename': 'filename'}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/evidences', body).json()
        identifier = response['id']
        body = {'filename': 'filename2'}
        response = self._subject.update(f'/api/v2/cases/{case_identifier}/evidences/{identifier}', body)
        self.assertEqual(200, response.status_code)

    def test_update_evidence_should_change_filename(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'filename': 'filename'}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/evidences', body).json()
        identifier = response['id']
        body = {'filename': 'filename2'}
        response = self._subject.update(f'/api/v2/cases/{case_identifier}/evidences/{identifier}', body).json()
        self.assertEqual('filename2', response['filename'])

    def test_update_evidence_should_change_file_size(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'filename': 'filename'}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/evidences', body).json()
        identifier = response['id']
        body = {'file_size': 30}
        response = self._subject.update(f'/api/v2/cases/{case_identifier}/evidences/{identifier}', body).json()
        self.assertEqual(30, response['file_size'])

    def test_update_evidence_should_change_type_id(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'filename': 'filename'}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/evidences', body).json()
        identifier = response['id']
        body = {'type_id': 3}
        response = self._subject.update(f'/api/v2/cases/{case_identifier}/evidences/{identifier}', body).json()
        self.assertEqual(3, response['type_id'])

    def test_update_evidence_should_change_start_date(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'filename': 'filename'}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/evidences', body).json()
        identifier = response['id']
        body = {'start_date': '2024-04-13T03:02:00'}
        response = self._subject.update(f'/api/v2/cases/{case_identifier}/evidences/{identifier}', body).json()
        self.assertEqual('2024-04-13T03:02:00', response['start_date'])

    def test_update_evidence_should_keep_file_uuid(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'filename': 'filename'}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/evidences', body).json()
        uuid = response['file_uuid']
        identifier = response['id']
        body = {'filename': 'filename2'}
        response = self._subject.update(f'/api/v2/cases/{case_identifier}/evidences/{identifier}', body).json()
        self.assertEqual(uuid, response['file_uuid'])

    def test_update_evidence_should_return_403_when_user_has_no_permission_to_access_case(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'filename': 'filename'}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/evidences', body).json()
        identifier = response['id']

        user = self._subject.create_dummy_user()
        body = {'filename': 'filename2'}
        response = user.update(f'/api/v2/cases/{case_identifier}/evidences/{identifier}', body)
        self.assertEqual(403, response.status_code)

    def test_update_evidence_should_return_404_when_case_does_not_exist(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'filename': 'filename'}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/evidences', body).json()
        identifier = response['id']
        body = {'filename': 'filename2'}
        response = self._subject.update(f'/api/v2/cases/{_IDENTIFIER_FOR_NONEXISTENT_OBJECT}/evidences/{identifier}', body)
        self.assertEqual(404, response.status_code)

    def test_update_evidence_should_return_400_when_case_identifier_does_not_match_evidence_case(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'filename': 'filename'}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/evidences', body).json()
        identifier = response['id']
        case_identifier2 = self._subject.create_dummy_case()
        body = {'filename': 'filename2'}
        response = self._subject.update(f'/api/v2/cases/{case_identifier2}/evidences/{identifier}', body)
        self.assertEqual(400, response.status_code)

    def test_update_evidence_should_return_404_when_evidence_does_not_exist(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'filename': 'filename2'}
        response = self._subject.update(f'/api/v2/cases/{case_identifier}/evidences/{_IDENTIFIER_FOR_NONEXISTENT_OBJECT}', body)
        self.assertEqual(404, response.status_code)

    def test_delete_evidence_should_return_204(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'filename': 'filename'}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/evidences', body).json()
        identifier = response['id']
        response = self._subject.delete(f'/api/v2/cases/{case_identifier}/evidences/{identifier}')
        self.assertEqual(204, response.status_code)

    def test_get_evidence_should_return_404_after_it_has_been_deleted(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'filename': 'filename'}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/evidences', body).json()
        identifier = response['id']
        self._subject.delete(f'/api/v2/cases/{case_identifier}/evidences/{identifier}')
        response = self._subject.get(f'/api/v2/cases/{case_identifier}/evidences/{identifier}')
        self.assertEqual(404, response.status_code)

    def test_delete_evidence_should_return_403_when_user_has_no_permission_to_access_case(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'filename': 'filename'}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/evidences', body).json()
        identifier = response['id']

        user = self._subject.create_dummy_user()
        response = user.delete(f'/api/v2/cases/{case_identifier}/evidences/{identifier}')
        self.assertEqual(403, response.status_code)

    def test_delete_evidence_should_not_remove_evidence_when_user_has_no_permission_to_access_case(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'filename': 'filename'}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/evidences', body).json()
        identifier = response['id']

        user = self._subject.create_dummy_user()
        user.delete(f'/api/v2/cases/{case_identifier}/evidences/{identifier}')
        response = self._subject.get(f'/api/v2/cases/{case_identifier}/evidences/{identifier}')
        self.assertEqual(200, response.status_code)

    def test_delete_evidence_should_return_404_when_case_does_not_exist(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'filename': 'filename'}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/evidences', body).json()
        identifier = response['id']
        response = self._subject.delete(f'/api/v2/cases/{_IDENTIFIER_FOR_NONEXISTENT_OBJECT}/evidences/{identifier}')
        self.assertEqual(404, response.status_code)

    def test_delete_evidence_should_return_404_when_evidence_does_not_exist(self):
        case_identifier = self._subject.create_dummy_case()
        response = self._subject.delete(f'/api/v2/cases/{case_identifier}/evidences/{_IDENTIFIER_FOR_NONEXISTENT_OBJECT}')
        self.assertEqual(404, response.status_code)

    def test_delete_evidence_should_return_400_when_case_identifier_does_not_match_evidence_case(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'filename': 'filename'}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/evidences', body).json()
        identifier = response['id']
        case_identifier2 = self._subject.create_dummy_case()
        response = self._subject.delete(f'/api/v2/cases/{case_identifier2}/evidences/{identifier}')
        self.assertEqual(400, response.status_code)
