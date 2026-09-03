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
from iris import IRIS_PERMISSION_ALERTS_READ
from iris import IRIS_PERMISSION_ALERTS_WRITE
from iris import IRIS_PERMISSION_ALERTS_DELETE

_IDENTIFIER_FOR_NONEXISTENT_OBJECT = 123456789


class TestsRestAlerts(TestCase):

    def setUp(self) -> None:
        self._subject = Iris()

    def tearDown(self):
        self._subject.clear_database()

    def test_create_alert_should_return_201(self):
        body = {
            'alert_title': 'title',
            'alert_severity_id': 4,
            'alert_status_id': 3,
            'alert_customer_id': 1,
        }
        response = self._subject.create('/api/v2/alerts', body)
        self.assertEqual(201, response.status_code)

    def test_create_alert_should_return_data_alert_title(self):
        body = {
            'alert_title': 'title',
            'alert_severity_id': 4,
            'alert_status_id': 3,
            'alert_customer_id': 1,
        }
        response = self._subject.create('/api/v2/alerts', body).json()
        self.assertEqual('title', response['alert_title'])

    def test_create_alert_should_return_data_alert_severity_id(self):
        body = {
            'alert_title': 'title',
            'alert_severity_id': 4,
            'alert_status_id': 3,
            'alert_customer_id': 1,
        }
        response = self._subject.create('/api/v2/alerts', body).json()
        self.assertEqual(4, response['alert_severity_id'])

    def test_create_alert_should_return_data_alert_status_id(self):
        body = {
            'alert_title': 'title',
            'alert_severity_id': 4,
            'alert_status_id': 3,
            'alert_customer_id': 1,
        }
        response = self._subject.create('/api/v2/alerts', body).json()
        self.assertEqual(3, response['alert_status_id'])

    def test_create_alert_should_return_data_alert_customer_id(self):
        body = {
            'alert_title': 'title',
            'alert_severity_id': 4,
            'alert_status_id': 3,
            'alert_customer_id': 1,
        }
        response = self._subject.create('/api/v2/alerts', body).json()
        self.assertEqual(1, response['alert_customer_id'])

    def test_create_alert_should_accept_the_fields_sent_by_the_manual_creation_form(self):
        body = {
            'alert_title': 'Hotline call from the finance team',
            'alert_description': 'User reports a convincing invoice phish.',
            'alert_severity_id': 4,
            'alert_status_id': 3,
            'alert_customer_id': 1,
            'alert_source': 'SOC hotline',
            'alert_source_ref': 'CALL-2026-0042',
            'alert_source_link': 'https://example.org/calls/42',
            'alert_source_event_time': '2026-09-03T14:30:00',
            'alert_note': 'Caller is forwarding the mail.',
            'alert_tags': 'phishing,hotline',
        }
        response = self._subject.create('/api/v2/alerts', body)
        self.assertEqual(201, response.status_code)

    def test_create_alert_should_return_alert_description(self):
        body = {
            'alert_title': 'title',
            'alert_description': 'description',
            'alert_severity_id': 4,
            'alert_status_id': 3,
            'alert_customer_id': 1,
        }
        response = self._subject.create('/api/v2/alerts', body).json()
        self.assertEqual('description', response['alert_description'])

    def test_create_alert_should_return_400_when_alert_customer_id_is_missing(self):
        body = {
            'alert_title': 'title',
            'alert_severity_id': 4,
            'alert_status_id': 3,
        }
        response = self._subject.create('/api/v2/alerts', body)
        self.assertEqual(400, response.status_code)

    def test_create_alert_should_return_403_when_user_has_no_permission_to_alert(self):
        user = self._subject.create_dummy_user()
        body = {
            'alert_title': 'title',
            'alert_severity_id': 4,
            'alert_status_id': 3,
            'alert_customer_id': 1,
        }
        response = user.create('/api/v2/alerts', body)
        self.assertEqual(403, response.status_code)

    def test_create_alert_should_return_field_classification_id_null_when_not_provided(self):
        body = {
            'alert_title': 'title',
            'alert_severity_id': 4,
            'alert_status_id': 3,
            'alert_customer_id': 1,
        }
        response = self._subject.create('/api/v2/alerts', body).json()
        self.assertIsNone(response['alert_classification_id'])

    def test_alerts_with_filter_alerts_assets_should_not_fail(self):
        response = self._subject.get('/api/v2/alerts', query_parameters={'alert_assets': 'some assert name'})
        self.assertEqual(200, response.status_code)

    def test_alerts_filter_with_filter_alert_iocs_should_not_fail(self):
        response = self._subject.get('api/v2/alerts', query_parameters={'alert_iocs': 'some ioc value'})
        self.assertEqual(200, response.status_code)

    def test_alerts_with_empty_value_custom_condition_on_integer_field_should_return_200(self):
        # Regression for GlitchTip #221: a custom_conditions leaf with value=""
        # on a bigint column (e.g. alert_owner_id) caused a DataError:
        # invalid input syntax for type bigint. Empty-value leaves should be skipped.
        import json
        conditions = json.dumps({'logic': 'and', 'conditions': [{'field': 'alert_owner_id', 'operator': 'eq', 'value': ''}]})
        response = self._subject.get('/api/v2/alerts', query_parameters={'custom_conditions': conditions})
        self.assertEqual(200, response.status_code)

    def test_alerts_with_unknown_custom_condition_field_should_return_400_not_500(self):
        # Regression for GlitchTip #222: a typo in the custom_conditions field name
        # (e.g. "alert_stat" instead of "alert_status_id") raised an unhandled
        # ValueError -> 500. Should return 400 instead.
        import json
        conditions = json.dumps({'logic': 'and', 'conditions': [{'field': 'alert_stat', 'operator': 'eq', 'value': ''}]})
        response = self._subject.get('/api/v2/alerts', query_parameters={'custom_conditions': conditions})
        self.assertEqual(400, response.status_code)

    def test_alerts_with_gte_custom_condition_should_return_200(self):
        # Regression: the conditions builder offers '>= greater or equal', but
        # build_condition implemented gte/lte only for JSON paths, so a gte on
        # a real column raised ValueError: Unsupported operator: gte -> 400.
        import json
        conditions = json.dumps({'logic': 'and', 'conditions': [{'field': 'alert_severity_id', 'operator': 'gte', 'value': '1'}]})
        response = self._subject.get('/api/v2/alerts', query_parameters={'custom_conditions': conditions})
        self.assertEqual(200, response.status_code)

    def test_alerts_with_lte_custom_condition_should_return_200(self):
        import json
        conditions = json.dumps({'logic': 'and', 'conditions': [{'field': 'alert_severity_id', 'operator': 'lte', 'value': '5'}]})
        response = self._subject.get('/api/v2/alerts', query_parameters={'custom_conditions': conditions})
        self.assertEqual(200, response.status_code)

    def test_get_alerts_filter_should_show_newly_created_alert_for_administrator(self):
        alert_title = 'title_test'
        body = {
            'alert_title': alert_title,
            'alert_severity_id': 4,
            'alert_status_id': 3,
            'alert_customer_id': 1
        }
        self._subject.create('/alerts/add', body)
        response = self._subject.get('/api/v2/alerts', query_parameters={'alert_title': alert_title}).json()
        self.assertEqual(1, response['total'])

    def test_get_alerts_should_return_field_data(self):
        response = self._subject.get('/api/v2/alerts').json()
        self.assertEqual([], response['data'])

    def _create_alert(self, alert_title, alert_severity_id=4):
        body = {
            'alert_title': alert_title,
            'alert_severity_id': alert_severity_id,
            'alert_status_id': 3,
            'alert_customer_id': 1,
        }
        return self._subject.create('/api/v2/alerts', body).json()

    def test_get_alerts_should_order_by_title_ascending(self):
        self._create_alert('charlie')
        self._create_alert('alpha')
        self._create_alert('bravo')
        response = self._subject.get('/api/v2/alerts',
                                     query_parameters={'order_by': 'title', 'sort': 'asc'}).json()
        self.assertEqual(['alpha', 'bravo', 'charlie'],
                         [alert['alert_title'] for alert in response['data']])

    def test_get_alerts_should_order_by_title_descending(self):
        self._create_alert('charlie')
        self._create_alert('alpha')
        self._create_alert('bravo')
        response = self._subject.get('/api/v2/alerts',
                                     query_parameters={'order_by': 'title', 'sort': 'desc'}).json()
        self.assertEqual(['charlie', 'bravo', 'alpha'],
                         [alert['alert_title'] for alert in response['data']])

    def test_get_alerts_should_order_by_severity_descending(self):
        self._create_alert('low', alert_severity_id=2)
        self._create_alert('high', alert_severity_id=5)
        self._create_alert('medium', alert_severity_id=3)
        response = self._subject.get('/api/v2/alerts',
                                     query_parameters={'order_by': 'severity', 'sort': 'desc'}).json()
        self.assertEqual([5, 3, 2], [alert['alert_severity_id'] for alert in response['data']])

    def test_get_alerts_should_fall_back_to_the_event_time_when_order_by_is_not_a_sortable_column(self):
        self._create_alert('charlie')
        self._create_alert('alpha')
        by_event_time = self._subject.get('/api/v2/alerts', query_parameters={'sort': 'asc'}).json()
        response = self._subject.get('/api/v2/alerts',
                                     query_parameters={'order_by': 'not_a_column', 'sort': 'asc'}).json()
        self.assertEqual([alert['alert_title'] for alert in by_event_time['data']],
                         [alert['alert_title'] for alert in response['data']])

    def test_get_grouped_alerts_should_order_by_title_ascending(self):
        self._create_alert('charlie')
        self._create_alert('alpha')
        self._create_alert('bravo')
        response = self._subject.get('/api/v2/alerts/grouped',
                                     query_parameters={'order_by': 'title', 'sort': 'asc'}).json()
        self.assertEqual(['alpha', 'bravo', 'charlie'],
                         [unit['alert']['alert_title'] for unit in response['data']])

    def test_merge_alert_into_a_case_should_not_fail(self):
        case_identifier = self._subject.create_dummy_case()
        body = {
            'alert_title': 'title',
            'alert_severity_id': 4,
            'alert_status_id': 3,
            'alert_customer_id': 1
        }
        response = self._subject.create('/alerts/add', body).json()
        alert_identifier = response['data']['alert_id']
        body = {
            'target_case_id': case_identifier,
            'iocs_import_list': [],
            'assets_import_list': []
        }
        response = self._subject.create(f'/alerts/merge/{alert_identifier}', body)
        # TODO should be 201
        self.assertEqual(200, response.status_code)

    def test_create_customer_should_return_400_when_user_has_customer_alert_right(self):
        group_identifier = self._subject.create_dummy_group([IRIS_PERMISSION_ALERTS_WRITE])
        user = self._subject.create_dummy_user()
        body = {'groups_membership': [group_identifier]}
        self._subject.create(f'/manage/users/{user.get_identifier()}/groups/update', body)

        body = {
            'alert_title': 'title',
            'alert_severity_id': 4,
            'alert_status_id': 3,
            'alert_customer_id': 1,
        }
        response = user.create('/api/v2/alerts', body)
        self.assertEqual(400, response.status_code)

    def test_get_alert_should_return_200(self):
        body = {
            'alert_title': 'title',
            'alert_severity_id': 4,
            'alert_status_id': 3,
            'alert_customer_id': 1
        }
        response = self._subject.create('api/v2/alerts', body).json()
        identifier = response['alert_id']
        response = self._subject.get(f'/api/v2/alerts/{identifier}')
        self.assertEqual(200, response.status_code)

    def test_get_alert_should_return_alert_title(self):
        alert_title = 'title_test'
        body = {
            'alert_title': alert_title,
            'alert_severity_id': 4,
            'alert_status_id': 3,
            'alert_customer_id': 1
        }
        response = self._subject.create('api/v2/alerts', body).json()
        identifier = response['alert_id']
        response = self._subject.get(f'/api/v2/alerts/{identifier}').json()
        self.assertEqual(alert_title, response['alert_title'])

    def test_get_alert_should_return_alert_uuid(self):
        body = {
            'alert_title': 'title',
            'alert_severity_id': 4,
            'alert_status_id': 3,
            'alert_customer_id': 1
        }
        response = self._subject.create('api/v2/alerts', body).json()
        identifier = response['alert_id']
        uuid = response['alert_uuid']
        response = self._subject.get(f'/api/v2/alerts/{identifier}').json()
        self.assertEqual(uuid, response['alert_uuid'])

    def test_get_alert_should_return_404_when_alert_not_found(self):
        response = self._subject.get(f'/api/v2/alerts/{_IDENTIFIER_FOR_NONEXISTENT_OBJECT}')
        self.assertEqual(404, response.status_code)

    def test_get_alert_should_return_403_when_user_has_no_permission_to_read_alert(self):
        user = self._subject.create_dummy_user()
        body = {
            'alert_title': 'title',
            'alert_severity_id': 4,
            'alert_status_id': 3,
            'alert_customer_id': 1,
        }
        response = self._subject.create('api/v2/alerts', body).json()
        identifier = response['alert_id']
        response = user.get(f'/api/v2/alerts/{identifier}')
        self.assertEqual(403, response.status_code)

    def test_get_alert_should_return_404_when_user_has_no_customer_access(self):
        group_identifier = self._subject.create_dummy_group([IRIS_PERMISSION_ALERTS_READ])
        user = self._subject.create_dummy_user()
        body = {'groups_membership': [group_identifier]}
        self._subject.create(f'/manage/users/{user.get_identifier()}/groups/update', body)

        body = {
            'alert_title': 'title',
            'alert_severity_id': 4,
            'alert_status_id': 3,
            'alert_customer_id': 1,
        }
        response = self._subject.create('/api/v2/alerts', body).json()
        identifier = response['alert_id']
        response = user.get(f'/api/v2/alerts/{identifier}')
        self.assertEqual(404, response.status_code)

    def test_update_alert_should_return_200(self):
        body = {
            'alert_title': 'title',
            'alert_severity_id': 4,
            'alert_status_id': 3,
            'alert_customer_id': 1
        }
        response = self._subject.create('api/v2/alerts', body).json()
        identifier = response['alert_id']
        response = self._subject.update(f'/api/v2/alerts/{identifier}', {'alert_title': 'new_title'})
        self.assertEqual(200, response.status_code)

    def test_update_alert_should_return_alert_title(self):
        body = {
            'alert_title': 'title',
            'alert_severity_id': 4,
            'alert_status_id': 3,
            'alert_customer_id': 1
        }
        response = self._subject.create('api/v2/alerts', body).json()
        identifier = response['alert_id']
        alert_title = 'new_title'
        response = self._subject.update(f'/api/v2/alerts/{identifier}', {'alert_title': alert_title}).json()
        self.assertEqual(alert_title, response['alert_title'])

    def test_update_alert_should_return_alert_description(self):
        body = {
            'alert_title': 'title',
            'alert_description': 'description',
            'alert_severity_id': 4,
            'alert_status_id': 3,
            'alert_customer_id': 1
        }
        response = self._subject.create('api/v2/alerts', body).json()
        identifier = response['alert_id']
        alert_description = 'new_description'
        response = self._subject.update(f'/api/v2/alerts/{identifier}', {'alert_description': alert_description}).json()
        self.assertEqual(alert_description, response['alert_description'])

    def test_update_alert_should_not_change_alert_title_when_only_description_is_updated(self):
        body = {
            'alert_title': 'title',
            'alert_description': 'description',
            'alert_severity_id': 4,
            'alert_status_id': 3,
            'alert_customer_id': 1
        }
        response = self._subject.create('api/v2/alerts', body).json()
        identifier = response['alert_id']
        response = self._subject.update(f'/api/v2/alerts/{identifier}', {'alert_description': 'new_description'}).json()
        self.assertEqual('title', response['alert_title'])

    def test_update_alert_should_return_alert_uuid(self):
        alert_title = 'new_title'
        body = {
            'alert_title': 'title',
            'alert_severity_id': 4,
            'alert_status_id': 3,
            'alert_customer_id': 1
        }
        response = self._subject.create('api/v2/alerts', body).json()
        identifier = response['alert_id']
        uuid = response['alert_uuid']
        response = self._subject.update(f'/api/v2/alerts/{identifier}', {'alert_title': alert_title}).json()
        self.assertEqual(uuid, response['alert_uuid'])

    def test_update_alert_should_return_404_when_alert_not_found(self):
        response = self._subject.update(f'/api/v2/alerts/{_IDENTIFIER_FOR_NONEXISTENT_OBJECT}', {'alert_title': 'alert_title'})
        self.assertEqual(404, response.status_code)

    def test_update_alert_should_return_403_when_user_has_no_permission_to_read_alert(self):
        user = self._subject.create_dummy_user()
        body = {
            'alert_title': 'title',
            'alert_severity_id': 4,
            'alert_status_id': 3,
            'alert_customer_id': 1,
        }
        response = self._subject.create('api/v2/alerts', body).json()
        identifier = response['alert_id']
        response = user.update(f'/api/v2/alerts/{identifier}', {})
        self.assertEqual(403, response.status_code)

    def test_update_alert_should_return_404_when_user_has_no_customer_access(self):
        group_identifier = self._subject.create_dummy_group([IRIS_PERMISSION_ALERTS_WRITE])
        user = self._subject.create_dummy_user()
        body = {'groups_membership': [group_identifier]}
        self._subject.create(f'/manage/users/{user.get_identifier()}/groups/update', body)

        body = {
            'alert_title': 'title',
            'alert_severity_id': 4,
            'alert_status_id': 3,
            'alert_customer_id': 1,
        }
        response = self._subject.create('/api/v2/alerts', body).json()
        identifier = response['alert_id']
        response = user.update(f'/api/v2/alerts/{identifier}', {'alert_title': 'new_title'})
        self.assertEqual(404, response.status_code)

    def test_update_alert_should_update_alert_context(self):
        body = {
            'alert_title': 'title',
            'alert_severity_id': 4,
            'alert_status_id': 3,
            'alert_customer_id': 1
        }
        response = self._subject.create('api/v2/alerts', body).json()
        identifier = response['alert_id']
        alert_context = {'context_key': 'key'}
        response = self._subject.update(f'/api/v2/alerts/{identifier}', {'alert_context': alert_context}).json()
        self.assertEqual(alert_context, response['alert_context'])

    def test_update_alert_should_update_alert_source_content(self):
        body = {
            'alert_title': 'title',
            'alert_severity_id': 4,
            'alert_status_id': 3,
            'alert_customer_id': 1
        }
        response = self._subject.create('api/v2/alerts', body).json()
        identifier = response['alert_id']
        alert_source_content = {
            '_id': '603f704aaf7417985bbf3b22',
            'contextId': '206e2965-6533-48a6-ba9e-794364a84bf9',
            'description': 'Contoso user performed 11 suspicious activities MITRE'
        }
        response = self._subject.update(f'/api/v2/alerts/{identifier}', {'alert_source_content': alert_source_content}).json()
        self.assertEqual(alert_source_content, response['alert_source_content'])

    def test_create_alert_should_return_asset_name_when_we_add_asset(self):
        asset_name = 'My super asset'
        body = {
            'alert_title': 'title',
            'alert_severity_id': 4,
            'alert_status_id': 3,
            'alert_customer_id': 1,
            'alert_assets': [{
            'asset_name': asset_name,
            'asset_description': 'Asset description',
            'asset_type_id': 1,
            'asset_ip': '1.1.1.1',
            'asset_domain': '',
            'asset_tags': 'tag1,tag2',
            }]
        }
        response = self._subject.create('/api/v2/alerts', body).json()
        self.assertEqual(asset_name, response['assets'][0]['asset_name'])

    def test_create_alert_should_return_ioc_value_when_we_add_ioc(self):
        ioc_value = 'Tarzan 5'
        body = {
            'alert_title': 'title',
            'alert_severity_id': 4,
            'alert_status_id': 3,
            'alert_customer_id': 1,
            'alert_iocs': [{
                'ioc_value': ioc_value,
                'ioc_description': 'description of Tarzan',
                'ioc_tlp_id': 1,
                'ioc_type_id': 2,
                'ioc_tags': 'tag1,tag2',
            }]
        }
        response = self._subject.create('/api/v2/alerts', body).json()
        self.assertEqual(ioc_value, response['iocs'][0]['ioc_value'])

    def test_delete_alert_should_return_204(self):
        body = {
            'alert_title': 'title',
            'alert_severity_id': 4,
            'alert_status_id': 3,
            'alert_customer_id': 1
        }
        response = self._subject.create('api/v2/alerts', body).json()
        identifier = response['alert_id']
        response = self._subject.delete(f'/api/v2/alerts/{identifier}')
        self.assertEqual(204, response.status_code)

    def test_delete_alert_should_return_404_when_alert_not_found(self):
        response = self._subject.delete(f'/api/v2/alerts/{_IDENTIFIER_FOR_NONEXISTENT_OBJECT}')
        self.assertEqual(404, response.status_code)

    def test_delete_alert_should_return_403_when_user_has_no_permission_to_delete_alert(self):
        user = self._subject.create_dummy_user()
        body = {
            'alert_title': 'title',
            'alert_severity_id': 4,
            'alert_status_id': 3,
            'alert_customer_id': 1
        }
        response = self._subject.create('api/v2/alerts', body).json()
        identifier = response['alert_id']
        response = user.delete(f'/api/v2/alerts/{identifier}')
        self.assertEqual(403, response.status_code)

    def test_delete_alert_should_return_404_when_user_has_no_customer_access(self):
        group_identifier = self._subject.create_dummy_group([IRIS_PERMISSION_ALERTS_DELETE])
        user = self._subject.create_dummy_user()
        body = {'groups_membership': [group_identifier]}
        self._subject.create(f'/manage/users/{user.get_identifier()}/groups/update', body)

        body = {
            'alert_title': 'title',
            'alert_severity_id': 4,
            'alert_status_id': 3,
            'alert_customer_id': 1,
        }
        response = self._subject.create('/api/v2/alerts', body).json()
        identifier = response['alert_id']
        response = user.delete(f'/api/v2/alerts/{identifier}')
        self.assertEqual(404, response.status_code)

    def test_get_alert_should_return_404_after_delete_alert(self):
        body = {
            'alert_title': 'title',
            'alert_severity_id': 4,
            'alert_status_id': 3,
            'alert_customer_id': 1
        }
        response = self._subject.create('api/v2/alerts', body).json()
        identifier = response['alert_id']
        self._subject.delete(f'/api/v2/alerts/{identifier}')
        response = self._subject.get(f'/api/v2/alerts/{identifier}')
        self.assertEqual(404, response.status_code)

    def test_get_related_alerts_should_return_200(self):
        body = {
            'alert_title': 'title',
            'alert_severity_id': 4,
            'alert_status_id': 3,
            'alert_customer_id': 1
        }
        response = self._subject.create('api/v2/alerts', body).json()
        identifier = response['alert_id']
        response = self._subject.get(f'/api/v2/alerts/{identifier}/related-alerts')
        self.assertEqual(200, response.status_code)

    def test_get_related_alerts_should_return_404_when_alert_not_found(self):
        response = self._subject.get(f'/api/v2/alerts/{_IDENTIFIER_FOR_NONEXISTENT_OBJECT}/related-alerts')
        self.assertEqual(404, response.status_code)

    def test_get_related_alerts_should_return_403_when_user_has_no_permission_to_get_alert(self):
        user = self._subject.create_dummy_user()
        body = {
            'alert_title': 'title',
            'alert_severity_id': 4,
            'alert_status_id': 3,
            'alert_customer_id': 1
        }
        response = self._subject.create('api/v2/alerts', body).json()
        identifier = response['alert_id']
        response = user.get(f'/api/v2/alerts/{identifier}/related-alerts')
        self.assertEqual(403, response.status_code)

    def test_get_related_alerts_should_return_404_when_user_has_no_customer_access(self):
        body = {
            'group_name': 'Customer read',
            'group_description': 'Group with customers can read alert',
            'group_permissions': [IRIS_PERMISSION_ALERTS_READ]
        }
        response = self._subject.create('/manage/groups/add', body).json()
        group_identifier = response['data']['group_id']
        user = self._subject.create_dummy_user()
        body = {'groups_membership': [group_identifier]}
        self._subject.create(f'/manage/users/{user.get_identifier()}/groups/update', body)

        body = {
            'alert_title': 'title',
            'alert_severity_id': 4,
            'alert_status_id': 3,
            'alert_customer_id': 1,
        }
        response = self._subject.create('/api/v2/alerts', body).json()
        identifier = response['alert_id']
        response = user.get(f'/api/v2/alerts/{identifier}/related-alerts')
        self.assertEqual(404, response.status_code)
