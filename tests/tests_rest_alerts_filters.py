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


class TestsRestAlertsFilters(TestCase):

    def setUp(self) -> None:
        self._subject = Iris()

    def tearDown(self):
        self._subject.clear_database()

    def test_create_alert_filter_should_return_201(self):
        body = {
            'filter_is_private': 'true',
            'filter_type': 'alerts',
            'filter_name': 'filter name',
            'filter_description': 'filter description',
            'filter_data' :{
                'alert_tilte': 'filter name',
                'alert_description': '',
                'alert_source': '',
                'alert_tags': '',
                'alert_severity_id': '',
                'alert_start_date': '',
                'source_start_date': '',
                'source_end_date': '',
                'creation_end_date': '',
                'creation_start_date': '',
                'alert_assets': '',
                'alert_iocs': '',
                'alert_ids': '',
                'source_reference': '',
                'case_id': '',
                'custom_conditions': '',

            },
        }
        response = self._subject.create('/api/v2/alerts-filters', body)
        self.assertEqual(201, response.status_code)
