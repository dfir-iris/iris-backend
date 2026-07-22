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


class TestsRestEvents(TestCase):

    def setUp(self) -> None:
        self._subject = Iris()

    def tearDown(self):
        self._subject.clear_database()

    def test_create_event_should_return_201(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'event_title': 'title', 'event_category_id': 1,
                'event_date': '2025-03-26T00:00:00.000', 'event_tz': '+00:00',
                'event_assets': [], 'event_iocs': []}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/events', body)
        self.assertEqual(201, response.status_code)

    def test_create_event_should_set_event_title(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'event_title': 'title', 'event_category_id': 1,
                'event_date': '2025-03-26T00:00:00.000', 'event_tz': '+00:00',
                'event_assets': [], 'event_iocs': []}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/events', body).json()
        self.assertEqual('title', response['event_title'])

    def test_create_event_should_return_400_when_field_event_title_is_missing(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'event_category_id': 1,
                'event_date': '2025-03-26T00:00:00.000', 'event_tz': '+00:00',
                'event_assets': [], 'event_iocs': []}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/events', body)
        self.assertEqual(400, response.status_code)

    def test_create_event_should_return_404_when_case_is_missing(self):
        body = {'event_title': 'title', 'event_category_id': 1,
                'event_date': '2025-03-26T00:00:00.000', 'event_tz': '+00:00',
                'event_assets': [], 'event_iocs': []}
        response = self._subject.create(f'/api/v2/cases/{_IDENTIFIER_FOR_NONEXISTENT_OBJECT}/events', body)
        self.assertEqual(404, response.status_code)

    def test_create_event_should_return_403_when_user_has_no_permission_to_access_case(self):
        case_identifier = self._subject.create_dummy_case()

        user = self._subject.create_dummy_user()
        body = {'event_title': 'title', 'event_category_id': 1,
                'event_date': '2025-03-26T00:00:00.000', 'event_tz': '+00:00',
                'event_assets': [], 'event_iocs': []}
        response = user.create(f'/api/v2/cases/{case_identifier}/events', body)
        self.assertEqual(403, response.status_code)

    def test_create_event_should_set_event_parent_id_when_provided(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'event_title': 'title', 'event_category_id': 1,
                'event_date': '2025-03-26T00:00:00.000', 'event_tz': '+00:00',
                'event_assets': [], 'event_iocs': []}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/events', body).json()
        identifier = response['event_id']
        body = {'event_title': 'title2', 'event_category_id': 1,
                'event_date': '2025-03-26T00:00:00.000', 'event_tz': '+00:00',
                'event_assets': [], 'event_iocs': [],
                'parent_event_id': identifier}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/events', body).json()
        self.assertEqual(identifier, response['parent_event_id'])

    def test_create_event_should_change_send_socket_io_message(self):
        case_identifier = self._subject.create_dummy_case()

        with self._subject.get_socket_io_client() as socket_io_client:
            socket_io_client.emit('join-case-obj-notif', f'case-{case_identifier}')

            body = {'event_title': 'title', 'event_category_id': 1,
                    'event_date': '2025-03-26T00:00:00.000', 'event_tz': '+00:00',
                    'event_assets': [], 'event_iocs': []}
            response = self._subject.create(f'/api/v2/cases/{case_identifier}/events', body).json()
            identifier = response['event_id']

            message = socket_io_client.receive()
            self.assertEqual(identifier, message['object_id'])

    def test_create_event_should_return_400_when_field_event_category_id_has_incorrect_type(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'event_title': 'title', 'event_category_id': 'wrong_event_category_id_type',
                'event_date': '2025-03-26T00:00:00.000', 'event_tz': '+00:00',
                'event_assets': [], 'event_iocs': []}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/events', body)
        self.assertEqual(400, response.status_code)

    def test_get_event_should_return_200(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'event_title': 'title', 'event_category_id': 1,
                'event_date': '2025-03-26T00:00:00.000', 'event_tz': '+00:00',
                'event_assets': [], 'event_iocs': []}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/events', body).json()
        identifier = response['event_id']
        response = self._subject.get(f'/api/v2/cases/{case_identifier}/events/{identifier}')
        self.assertEqual(200, response.status_code)

    def test_get_event_should_return_event_title(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'event_title': 'title', 'event_category_id': 1,
                'event_date': '2025-03-26T00:00:00.000', 'event_tz': '+00:00',
                'event_assets': [], 'event_iocs': []}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/events', body).json()
        identifier = response['event_id']
        response = self._subject.get(f'/api/v2/cases/{case_identifier}/events/{identifier}').json()
        self.assertEqual('title', response['event_title'])

    def test_get_event_should_return_event_category_id(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'event_title': 'title', 'event_category_id': 1,
                'event_date': '2025-03-26T00:00:00.000', 'event_tz': '+00:00',
                'event_assets': [], 'event_iocs': []}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/events', body).json()
        identifier = response['event_id']
        response = self._subject.get(f'/api/v2/cases/{case_identifier}/events/{identifier}').json()
        self.assertEqual(1, response['event_category_id'])

    def test_get_event_should_return_404_when_event_does_not_exist(self):
        case_identifier = self._subject.create_dummy_case()
        response = self._subject.get(f'/api/v2/cases/{case_identifier}/events/{_IDENTIFIER_FOR_NONEXISTENT_OBJECT}')
        self.assertEqual(404, response.status_code)

    def test_get_event_should_return_404_when_case_does_not_exist(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'event_title': 'title', 'event_category_id': 1,
                'event_date': '2025-03-26T00:00:00.000', 'event_tz': '+00:00',
                'event_assets': [], 'event_iocs': []}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/events', body).json()
        identifier = response['event_id']
        response = self._subject.get(f'/api/v2/cases/{_IDENTIFIER_FOR_NONEXISTENT_OBJECT}/events/{identifier}')
        self.assertEqual(404, response.status_code)

    def test_get_event_should_return_403_when_user_has_no_access_to_case(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'event_title': 'title', 'event_category_id': 1,
                'event_date': '2025-03-26T00:00:00.000', 'event_tz': '+00:00',
                'event_assets': [], 'event_iocs': []}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/events', body).json()
        identifier = response['event_id']

        user = self._subject.create_dummy_user()
        response = user.get(f'/api/v2/cases/{case_identifier}/events/{identifier}')
        self.assertEqual(403, response.status_code)

    def test_get_event_should_return_400_when_case_identifier_does_not_match_event_case(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'event_title': 'title', 'event_category_id': 1,
                'event_date': '2025-03-26T00:00:00.000', 'event_tz': '+00:00',
                'event_assets': [], 'event_iocs': []}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/events', body).json()
        identifier = response['event_id']
        case_identifier2 = self._subject.create_dummy_case()
        response = self._subject.get(f'/api/v2/cases/{case_identifier2}/events/{identifier}')
        self.assertEqual(400, response.status_code)

    def test_get_event_should_return_children_when_event_is_parent_of_another_event(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'event_title': 'title', 'event_category_id': 1,
                'event_date': '2025-03-26T00:00:00.000', 'event_tz': '+00:00',
                'event_assets': [], 'event_iocs': []}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/events', body).json()
        identifier = response['event_id']
        body = {'event_title': 'title2', 'event_category_id': 1,
                'event_date': '2025-03-26T00:00:00.000', 'event_tz': '+00:00',
                'event_assets': [], 'event_iocs': [],
                'parent_event_id': identifier}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/events', body).json()
        child_identifier = response['event_id']
        response = self._subject.get(f'/api/v2/cases/{case_identifier}/events/{identifier}', body).json()
        self.assertEqual(child_identifier, response['children'][0]['event_id'])

    def test_update_event_should_return_200(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'event_title': 'title', 'event_category_id': 1,
                'event_date': '2025-03-26T00:00:00.000', 'event_tz': '+00:00',
                'event_assets': [], 'event_iocs': []}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/events', body).json()
        identifier = response['event_id']
        body = {'event_title': 'new title', 'event_category_id': 1,
                'event_date': '2025-03-26T00:00:00.000', 'event_tz': '+00:00',
                'event_assets': [], 'event_iocs': []}
        response = self._subject.update(f'/api/v2/cases/{case_identifier}/events/{identifier}', body)
        self.assertEqual(200, response.status_code)

    def test_update_event_should_change_event_title(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'event_title': 'title', 'event_category_id': 1,
                'event_date': '2025-03-26T00:00:00.000', 'event_tz': '+00:00',
                'event_assets': [], 'event_iocs': []}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/events', body).json()
        identifier = response['event_id']
        body = {'event_title': 'new title', 'event_category_id': 1,
                'event_date': '2025-03-26T00:00:00.000', 'event_tz': '+00:00',
                'event_assets': [], 'event_iocs': []}
        response = self._subject.update(f'/api/v2/cases/{case_identifier}/events/{identifier}', body).json()
        self.assertEqual('new title', response['event_title'])

    def test_update_event_should_change_send_socket_io_message(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'event_title': 'title', 'event_category_id': 1,
                'event_date': '2025-03-26T00:00:00.000', 'event_tz': '+00:00',
                'event_assets': [], 'event_iocs': []}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/events', body).json()
        identifier = response['event_id']

        with self._subject.get_socket_io_client() as socket_io_client:
            socket_io_client.emit('join-case-obj-notif', f'case-{case_identifier}')

            body = {'event_title': 'new title', 'event_category_id': 1,
                    'event_date': '2025-03-26T00:00:00.000', 'event_tz': '+00:00',
                    'event_assets': [], 'event_iocs': []}
            self._subject.update(f'/api/v2/cases/{case_identifier}/events/{identifier}', body).json()

            message = socket_io_client.receive()

            self.assertEqual(identifier, message['object_id'])

    def test_socket_io_join_should_not_fail(self):
        case_identifier = self._subject.create_dummy_case()

        with self._subject.get_socket_io_client() as socket_io_client:
            socket_io_client.emit('join', f'case-{case_identifier}')
            message = socket_io_client.receive()
            self.assertEqual('administrator just joined', message['message'])

    def test_update_event_should_return_403_when_user_has_no_permission_to_access_case(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'event_title': 'title', 'event_category_id': 1,
                'event_date': '2025-03-26T00:00:00.000', 'event_tz': '+00:00',
                'event_assets': [], 'event_iocs': []}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/events', body).json()
        identifier = response['event_id']

        user = self._subject.create_dummy_user()
        body = {'event_title': 'new title', 'event_category_id': 1,
                'event_date': '2025-03-26T00:00:00.000', 'event_tz': '+00:00',
                'event_assets': [], 'event_iocs': []}
        response = user.update(f'/api/v2/cases/{case_identifier}/events/{identifier}', body)
        self.assertEqual(403, response.status_code)

    def test_update_event_should_return_404_when_event_does_not_exist(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'event_title': 'new title', 'event_category_id': 1,
                'event_date': '2025-03-26T00:00:00.000', 'event_tz': '+00:00',
                'event_assets': [], 'event_iocs': []}
        response = self._subject.update(f'/api/v2/cases/{case_identifier}/events/{_IDENTIFIER_FOR_NONEXISTENT_OBJECT}', body)
        self.assertEqual(404, response.status_code)

    def test_update_event_should_return_404_when_case_does_not_exist(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'event_title': 'title', 'event_category_id': 1,
                'event_date': '2025-03-26T00:00:00.000', 'event_tz': '+00:00',
                'event_assets': [], 'event_iocs': []}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/events', body).json()
        identifier = response['event_id']
        body = {'event_title': 'new title', 'event_category_id': 1,
                'event_date': '2025-03-26T00:00:00.000', 'event_tz': '+00:00',
                'event_assets': [], 'event_iocs': []}
        response = self._subject.update(f'/api/v2/cases/{_IDENTIFIER_FOR_NONEXISTENT_OBJECT}/events/{identifier}', body)
        self.assertEqual(404, response.status_code)

    def test_update_event_should_return_400_when_event_date_format_is_incorrect(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'event_title': 'title', 'event_category_id': 1,
                'event_date': '2025-03-26T00:00:00.000', 'event_tz': '+00:00',
                'event_assets': [], 'event_iocs': []}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/events', body).json()
        identifier = response['event_id']
        body = {'event_title': 'new title', 'event_category_id': 1,
                'event_date': '1744181930.204785', 'event_tz': '+00:00',
                'event_assets': [], 'event_iocs': []}
        response = self._subject.update(f'/api/v2/cases/{case_identifier}/events/{identifier}', body)
        self.assertEqual(400, response.status_code)

    def test_update_event_should_return_400_when_case_identifier_does_not_match_event_case(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'event_title': 'title', 'event_category_id': 1,
                'event_date': '2025-03-26T00:00:00.000', 'event_tz': '+00:00',
                'event_assets': [], 'event_iocs': []}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/events', body).json()
        identifier = response['event_id']
        case_identifier2 = self._subject.create_dummy_case()
        body = {'event_title': 'new title', 'event_category_id': 1,
                'event_date': '2025-03-26T00:00:00.000', 'event_tz': '+00:00',
                'event_assets': [], 'event_iocs': []}
        response = self._subject.update(f'/api/v2/cases/{case_identifier2}/events/{identifier}', body)
        self.assertEqual(400, response.status_code)

    def test_update_event_should_return_400_when_field_event_category_id_is_missing(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'event_title': 'title', 'event_category_id': 1,
                'event_date': '2025-03-26T00:00:00.000', 'event_tz': '+00:00',
                'event_assets': [], 'event_iocs': []}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/events', body).json()
        identifier = response['event_id']
        body = {'event_title': 'new title',
                'event_date': '2025-03-26T00:00:00.000', 'event_tz': '+00:00',
                'event_assets': [], 'event_iocs': []}
        response = self._subject.update(f'/api/v2/cases/{case_identifier}/events/{identifier}', body)
        self.assertEqual(400, response.status_code)

    def test_update_event_should_return_400_when_field_event_assets_is_missing(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'event_title': 'title', 'event_category_id': 1,
                'event_date': '2025-03-26T00:00:00.000', 'event_tz': '+00:00',
                'event_assets': [], 'event_iocs': []}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/events', body).json()
        identifier = response['event_id']
        body = {'event_title': 'new title', 'event_category_id': 1,
                'event_date': '2025-03-26T00:00:00.000', 'event_tz': '+00:00',
                'event_iocs': []}
        response = self._subject.update(f'/api/v2/cases/{case_identifier}/events/{identifier}', body)
        self.assertEqual(400, response.status_code)

    def test_update_event_should_return_400_when_field_event_iocs_is_missing(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'event_title': 'title', 'event_category_id': 1,
                'event_date': '2025-03-26T00:00:00.000', 'event_tz': '+00:00',
                'event_assets': [], 'event_iocs': []}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/events', body).json()
        identifier = response['event_id']
        body = {'event_title': 'new title', 'event_category_id': 1,
                'event_date': '2025-03-26T00:00:00.000', 'event_tz': '+00:00',
                'event_assets': []}
        response = self._subject.update(f'/api/v2/cases/{case_identifier}/events/{identifier}', body)
        self.assertEqual(400, response.status_code)

    def test_update_event_should_set_event_parent_id_when_provided(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'event_title': 'title', 'event_category_id': 1,
                'event_date': '2025-03-26T00:00:00.000', 'event_tz': '+00:00',
                'event_assets': [], 'event_iocs': []}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/events', body).json()
        parent_event_identifier = response['event_id']
        body = {'event_title': 'title2', 'event_category_id': 1,
                'event_date': '2025-03-26T00:00:00.000', 'event_tz': '+00:00',
                'event_assets': [], 'event_iocs': []}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/events', body).json()
        identifier = response['event_id']
        body = {'event_title': 'new title', 'event_category_id': 1,
                'event_date': '2025-03-26T00:00:00.000', 'event_tz': '+00:00',
                'event_assets': [], 'event_iocs': [],
                'parent_event_id': parent_event_identifier}
        response = self._subject.update(f'/api/v2/cases/{case_identifier}/events/{identifier}', body).json()
        self.assertEqual(parent_event_identifier, response['parent_event_id'])

    def test_delete_event_should_return_204(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'event_title': 'title', 'event_category_id': 1,
                'event_date': '2025-03-26T00:00:00.000', 'event_tz': '+00:00',
                'event_assets': [], 'event_iocs': []}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/events', body).json()
        identifier = response['event_id']
        response = self._subject.delete(f'/api/v2/cases/{case_identifier}/events/{identifier}')
        self.assertEqual(204, response.status_code)

    def test_get_event_should_return_404_after_it_has_been_deleted(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'event_title': 'title', 'event_category_id': 1,
                'event_date': '2025-03-26T00:00:00.000', 'event_tz': '+00:00',
                'event_assets': [], 'event_iocs': []}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/events', body).json()
        identifier = response['event_id']
        self._subject.delete(f'/api/v2/cases/{case_identifier}/events/{identifier}')
        response = self._subject.get(f'/api/v2/cases/{case_identifier}/events/{identifier}')
        self.assertEqual(404, response.status_code)

    def test_delete_event_should_return_404_when_case_does_not_exist(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'event_title': 'title', 'event_category_id': 1,
                'event_date': '2025-03-26T00:00:00.000', 'event_tz': '+00:00',
                'event_assets': [], 'event_iocs': []}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/events', body).json()
        identifier = response['event_id']
        response = self._subject.delete(f'/api/v2/cases/{_IDENTIFIER_FOR_NONEXISTENT_OBJECT}/events/{identifier}')
        self.assertEqual(404, response.status_code)

    def test_delete_event_should_return_404_when_the_event_does_not_exist(self):
        case_identifier = self._subject.create_dummy_case()
        response = self._subject.delete(f'/api/v2/cases/{case_identifier}/events/{_IDENTIFIER_FOR_NONEXISTENT_OBJECT}')
        self.assertEqual(404, response.status_code)

    def test_delete_event_should_return_403_when_user_has_no_permission_to_access_case(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'event_title': 'title', 'event_category_id': 1,
                'event_date': '2025-03-26T00:00:00.000', 'event_tz': '+00:00',
                'event_assets': [], 'event_iocs': []}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/events', body).json()
        identifier = response['event_id']

        user = self._subject.create_dummy_user()
        response = user.delete(f'/api/v2/cases/{case_identifier}/events/{identifier}')
        self.assertEqual(403, response.status_code)

    def test_delete_event_should_return_400_when_case_identifier_does_not_match_event_case(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'event_title': 'title', 'event_category_id': 1,
                'event_date': '2025-03-26T00:00:00.000', 'event_tz': '+00:00',
                'event_assets': [], 'event_iocs': []}
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/events', body).json()
        identifier = response['event_id']
        case_identifier2 = self._subject.create_dummy_case()
        response = self._subject.delete(f'/api/v2/cases/{case_identifier2}/events/{identifier}')
        self.assertEqual(400, response.status_code)

    def test_list_events_should_return_200_and_v2_envelope(self):
        case_identifier = self._subject.create_dummy_case()
        response = self._subject.get(f'/api/v2/cases/{case_identifier}/events')
        self.assertEqual(200, response.status_code)
        payload = response.json()
        # The v2 envelope is the SPA-friendly one: tim, pagination, state,
        # comments_map, assets, iocs, categories — never the legacy `timeline`
        # or `data`-wrapped shapes.
        for expected_key in ('tim', 'pagination', 'state', 'comments_map', 'assets', 'iocs', 'categories'):
            self.assertIn(expected_key, payload)
        self.assertNotIn('timeline', payload)

    def test_list_events_should_include_created_event(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'event_title': 'first', 'event_category_id': 1,
                'event_date': '2025-03-26T00:00:00.000', 'event_tz': '+00:00',
                'event_assets': [], 'event_iocs': []}
        self._subject.create(f'/api/v2/cases/{case_identifier}/events', body)
        response = self._subject.get(f'/api/v2/cases/{case_identifier}/events').json()
        titles = [ev['event_title'] for ev in response['tim']]
        self.assertIn('first', titles)

    def test_list_events_should_filter_by_title_query_param(self):
        case_identifier = self._subject.create_dummy_case()
        for title in ('alpha match', 'beta only'):
            body = {'event_title': title, 'event_category_id': 1,
                    'event_date': '2025-03-26T00:00:00.000', 'event_tz': '+00:00',
                    'event_assets': [], 'event_iocs': []}
            self._subject.create(f'/api/v2/cases/{case_identifier}/events', body)
        response = self._subject.get(f'/api/v2/cases/{case_identifier}/events',
                                     query_parameters={'title': 'alpha'}).json()
        titles = [ev['event_title'] for ev in response['tim']]
        self.assertEqual(['alpha match'], titles)

    def test_list_events_should_return_404_when_case_is_missing(self):
        response = self._subject.get(f'/api/v2/cases/{_IDENTIFIER_FOR_NONEXISTENT_OBJECT}/events')
        self.assertEqual(404, response.status_code)

    def test_list_events_should_return_403_when_user_has_no_permission(self):
        case_identifier = self._subject.create_dummy_case()
        user = self._subject.create_dummy_user()
        response = user.get(f'/api/v2/cases/{case_identifier}/events')
        self.assertEqual(403, response.status_code)

    def test_list_events_should_paginate_with_per_page(self):
        case_identifier = self._subject.create_dummy_case()
        for i in range(3):
            body = {'event_title': f'event-{i}', 'event_category_id': 1,
                    'event_date': f'2025-03-{20 + i:02d}T00:00:00.000', 'event_tz': '+00:00',
                    'event_assets': [], 'event_iocs': []}
            self._subject.create(f'/api/v2/cases/{case_identifier}/events', body)
        response = self._subject.get(f'/api/v2/cases/{case_identifier}/events',
                                     query_parameters={'per_page': 2, 'page': 1}).json()
        pagination = response['pagination']
        self.assertEqual(3, pagination['total'])
        self.assertEqual(2, pagination['last_page'])
        self.assertEqual(1, pagination['current_page'])
        self.assertEqual(2, pagination['next_page'])

    def test_v1_advanced_filter_should_still_work_with_deprecation_header(self):
        # External integrations hitting v1 must keep working; the server just
        # advertises the v2 alternative via response headers.
        case_identifier = self._subject.create_dummy_case()
        body = {'event_title': 'legacy path', 'event_category_id': 1,
                'event_date': '2025-03-26T00:00:00.000', 'event_tz': '+00:00',
                'event_assets': [], 'event_iocs': []}
        self._subject.create(f'/api/v2/cases/{case_identifier}/events', body)
        # v1 requires cid + q (JSON-encoded filter dict).
        response = self._subject.get('/case/timeline/advanced-filter',
                                     query_parameters={'cid': case_identifier, 'q': '{}'})
        self.assertEqual(200, response.status_code)
        self.assertEqual('true', response.headers.get('Deprecation'))
        link = response.headers.get('Link', '')
        self.assertIn('/api/v2/cases/', link)
