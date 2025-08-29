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
from iris import IRIS_PERMISSION_ALERTS_READ

_IDENTIFIER_FOR_NONEXISTENT_OBJECT = 123456789


class TestsRestComments(TestCase):

    def setUp(self) -> None:
        self._subject = Iris()

    def tearDown(self):
        self._subject.clear_database()

    def test_get_comments_should_return_200(self):
        body = {
            'alert_title': 'title',
            'alert_severity_id': 4,
            'alert_status_id': 3,
            'alert_customer_id': 1,
        }
        response = self._subject.create('/api/v2/alerts', body).json()
        object_identifier = response['alert_id']
        response = self._subject.get(f'/api/v2/alerts/{object_identifier}/comments')
        self.assertEqual(200, response.status_code)

    def test_get_comments_should_return_404_when_alert_is_not_found(self):
        response = self._subject.get(f'/api/v2/alerts/{_IDENTIFIER_FOR_NONEXISTENT_OBJECT}/comments')
        self.assertEqual(404, response.status_code)

    def get_comments_should_return_403_when_user_has_no_permission_to_read_alerts(self):
        body = {
            'alert_title': 'title',
            'alert_severity_id': 4,
            'alert_status_id': 3,
            'alert_customer_id': 1,
        }
        response = self._subject.create('/api/v2/alerts', body).json()
        object_identifier = response['alert_id']
        user = self._subject.create_dummy_user()
        response = user.get(f'/api/v2/alerts/{object_identifier}/comments')
        self.assertEqual(403, response.status_code)

    def get_comments_should_return_404_when_user_has_no_access_to_alerts_customer(self):
        customer_identifier = self._subject.create_dummy_customer()
        body = {
            'alert_title': 'title',
            'alert_severity_id': 4,
            'alert_status_id': 3,
            'alert_customer_id': customer_identifier,
        }
        response = self._subject.create('/api/v2/alerts', body).json()
        object_identifier = response['alert_id']

        group_identifier = self._subject.create_dummy_group([IRIS_PERMISSION_ALERTS_READ])
        user = self._subject.create_dummy_user()
        body = {'groups_membership': [group_identifier]}
        self._subject.create(f'/manage/users/{user.get_identifier()}/groups/update', body)
        response = user.get(f'/api/v2/alerts/{object_identifier}/comments')
        self.assertEqual(404, response.status_code)
