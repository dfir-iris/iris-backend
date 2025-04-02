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

    def test_get_event_should_return_404_when_evidence_does_not_exist(self):
        case_identifier = self._subject.create_dummy_case()
        response = self._subject.get(f'/api/v2/cases/{case_identifier}/events/{_IDENTIFIER_FOR_NONEXISTENT_OBJECT}')
        self.assertEqual(404, response.status_code)
