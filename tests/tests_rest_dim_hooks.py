#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
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
from iris import IRIS_INITIAL_CUSTOMER_IDENTIFIER
from iris import IRIS_PERMISSION_ALERTS_READ
from iris import IRIS_PERMISSION_ALERTS_WRITE


class TestsRestDimHooks(TestCase):

    def setUp(self) -> None:
        self._subject = Iris()

    def tearDown(self):
        self._subject.clear_database()

    # ------------------------------------------------------------------
    # GET /api/v2/dim-hooks?target=<type>
    # ------------------------------------------------------------------
    def test_list_hooks_returns_200_and_array(self):
        response = self._subject.get('/api/v2/dim-hooks', query_parameters={'target': 'ioc'})
        self.assertEqual(200, response.status_code)
        self.assertIsInstance(response.json(), list)

    def test_list_hooks_entries_carry_expected_shape(self):
        response = self._subject.get('/api/v2/dim-hooks', query_parameters={'target': 'ioc'}).json()
        for entry in response:
            self.assertIn('hook_name', entry)
            self.assertIn('manual_hook_ui_name', entry)
            self.assertIn('module_name', entry)

    def test_list_hooks_returns_empty_array_for_unknown_target(self):
        response = self._subject.get('/api/v2/dim-hooks', query_parameters={'target': 'no-such-type'}).json()
        self.assertEqual([], response)

    def test_list_hooks_returns_400_when_target_missing(self):
        response = self._subject.get('/api/v2/dim-hooks')
        self.assertEqual(400, response.status_code)

    def test_list_hooks_surfaces_iris_check_when_module_enabled(self):
        module_identifier = self._subject.get_module_identifier_by_name('IrisCheck')
        self._subject.create(f'/manage/modules/enable/{module_identifier}', {})
        try:
            entries = self._subject.get('/api/v2/dim-hooks', query_parameters={'target': 'ioc'}).json()
            module_names = [e['module_name'] for e in entries]
            self.assertIn('iris_check_module', module_names)
        finally:
            self._subject.create(f'/manage/modules/disable/{module_identifier}', {})

    # ------------------------------------------------------------------
    # POST /api/v2/cases/<cid>/dim-hooks/invoke
    # ------------------------------------------------------------------
    def _invoke(self, case_identifier, body):
        return self._subject.create(
            f'/api/v2/cases/{case_identifier}/dim-hooks/invoke', body,
        )

    def test_invoke_returns_400_when_hook_name_missing(self):
        case_identifier = self._subject.create_dummy_case()
        response = self._invoke(case_identifier, {'type': 'case', 'targets': [case_identifier]})
        self.assertEqual(400, response.status_code)

    def test_invoke_returns_400_when_targets_missing(self):
        case_identifier = self._subject.create_dummy_case()
        response = self._invoke(case_identifier, {'hook_name': 'on_manual_trigger_case', 'type': 'case'})
        self.assertEqual(400, response.status_code)

    def test_invoke_returns_400_when_data_type_missing(self):
        case_identifier = self._subject.create_dummy_case()
        response = self._invoke(case_identifier, {'hook_name': 'on_manual_trigger_case', 'targets': [1]})
        self.assertEqual(400, response.status_code)

    def test_invoke_returns_400_when_data_type_unsupported(self):
        case_identifier = self._subject.create_dummy_case()
        response = self._invoke(
            case_identifier,
            {'hook_name': 'on_manual_trigger_x', 'type': 'no-such-kind', 'targets': [1]},
        )
        self.assertEqual(400, response.status_code)

    def test_invoke_returns_400_when_target_not_an_int(self):
        case_identifier = self._subject.create_dummy_case()
        response = self._invoke(
            case_identifier,
            {'hook_name': 'on_manual_trigger_asset', 'type': 'asset', 'targets': ['not-a-number']},
        )
        self.assertEqual(400, response.status_code)

    def test_invoke_returns_queued_1_for_case_target(self):
        case_identifier = self._subject.create_dummy_case()
        response = self._invoke(
            case_identifier,
            {'hook_name': 'on_manual_trigger_case', 'type': 'case', 'targets': [case_identifier]},
        )
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual(1, body['queued'])

    def test_invoke_returns_queued_1_and_logs_when_asset_target_missing(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'asset_type_id': 1, 'asset_name': 'admin_laptop_test'}
        asset = self._subject.create(
            f'/api/v2/cases/{case_identifier}/assets', body,
        ).json()
        asset_identifier = asset['asset_id']
        response = self._invoke(
            case_identifier,
            {
                'hook_name': 'on_manual_trigger_asset',
                'type': 'asset',
                'targets': [asset_identifier, 999999999],
            },
        )
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual(1, body['queued'])
        self.assertIn('logs', body)
        self.assertTrue(any('999999999' in log for log in body['logs']))

    def test_invoke_returns_403_when_user_has_no_case_access(self):
        case_identifier = self._subject.create_dummy_case()
        user = self._subject.create_dummy_user()
        response = user.create(
            f'/api/v2/cases/{case_identifier}/dim-hooks/invoke',
            {'hook_name': 'on_manual_trigger_case', 'type': 'case', 'targets': [case_identifier]},
        )
        self.assertEqual(403, response.status_code)

    def test_invoke_returns_400_for_alert_data_type(self):
        # Alerts have no case to be scoped by — they go through
        # /api/v2/alerts/dim-hooks/invoke instead.
        case_identifier = self._subject.create_dummy_case()
        response = self._invoke(
            case_identifier,
            {'hook_name': 'on_manual_trigger_alert', 'type': 'alert', 'targets': [1]},
        )
        self.assertEqual(400, response.status_code)

    # ------------------------------------------------------------------
    # POST /api/v2/alerts/dim-hooks/invoke
    # ------------------------------------------------------------------
    def _create_alert(self, customer_identifier=IRIS_INITIAL_CUSTOMER_IDENTIFIER):
        body = {
            'alert_title': 'title',
            'alert_severity_id': 4,
            'alert_status_id': 3,
            'alert_customer_id': customer_identifier,
        }
        return self._subject.create('/api/v2/alerts', body).json()['alert_id']

    def _invoke_alerts(self, body):
        return self._subject.create('/api/v2/alerts/dim-hooks/invoke', body)

    def test_invoke_alerts_returns_400_when_targets_missing(self):
        response = self._invoke_alerts({'hook_name': 'on_manual_trigger_alert'})
        self.assertEqual(400, response.status_code)

    def test_invoke_alerts_returns_400_when_hook_name_missing(self):
        alert_identifier = self._create_alert()
        response = self._invoke_alerts({'targets': [alert_identifier]})
        self.assertEqual(400, response.status_code)

    def test_invoke_alerts_returns_400_when_hook_name_is_not_an_alert_hook(self):
        alert_identifier = self._create_alert()
        response = self._invoke_alerts({
            'hook_name': 'on_manual_trigger_ioc',
            'targets': [alert_identifier],
        })
        self.assertEqual(400, response.status_code)

    def test_invoke_alerts_returns_400_when_targets_is_not_a_list(self):
        response = self._invoke_alerts({
            'hook_name': 'on_manual_trigger_alert',
            'targets': 'nope',
        })
        self.assertEqual(400, response.status_code)

    def test_invoke_alerts_returns_400_when_target_not_an_int(self):
        response = self._invoke_alerts({
            'hook_name': 'on_manual_trigger_alert',
            'targets': ['not-a-number'],
        })
        self.assertEqual(400, response.status_code)

    def test_invoke_alerts_returns_queued_1_for_alert_target(self):
        alert_identifier = self._create_alert()
        response = self._invoke_alerts({
            'hook_name': 'on_manual_trigger_alert',
            'targets': [alert_identifier],
        })
        self.assertEqual(200, response.status_code)
        self.assertEqual(1, response.json()['queued'])

    def test_invoke_alerts_returns_queued_1_and_logs_when_target_missing(self):
        alert_identifier = self._create_alert()
        response = self._invoke_alerts({
            'hook_name': 'on_manual_trigger_alert',
            'targets': [alert_identifier, 999999999],
        })
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual(1, body['queued'])
        self.assertTrue(any('999999999' in log for log in body['logs']))

    def test_invoke_alerts_returns_403_when_user_lacks_alerts_write(self):
        alert_identifier = self._create_alert()
        user = self._subject.create_dummy_user(permissions=[IRIS_PERMISSION_ALERTS_READ])
        response = user.create(
            '/api/v2/alerts/dim-hooks/invoke',
            {'hook_name': 'on_manual_trigger_alert', 'targets': [alert_identifier]},
        )
        self.assertEqual(403, response.status_code)

    def test_invoke_alerts_skips_alert_of_a_customer_the_user_cannot_see(self):
        other_customer = self._subject.create_dummy_customer()
        alert_identifier = self._create_alert(customer_identifier=other_customer)
        user = self._subject.create_dummy_user(
            permissions=[IRIS_PERMISSION_ALERTS_READ, IRIS_PERMISSION_ALERTS_WRITE]
        )
        self._subject.create(
            f'/manage/users/{user.get_identifier()}/customers/update',
            {'customers_membership': [IRIS_INITIAL_CUSTOMER_IDENTIFIER]},
        )

        response = user.create(
            '/api/v2/alerts/dim-hooks/invoke',
            {'hook_name': 'on_manual_trigger_alert', 'targets': [alert_identifier]},
        )

        self.assertEqual(200, response.status_code)
        self.assertEqual(0, response.json()['queued'])
