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

import time
from unittest import TestCase

from iris import Iris

_IDENTIFIER_FOR_NONEXISTENT_OBJECT = 123456789
_WEBHOOKS = '/api/v2/manage/webhooks'


def _body(**overrides):
    body = {
        'name': 'Receiver',
        'enabled': True,
        'events': ['on_postload_case_create'],
        'url': 'https://hooks.example.org/in',
        'headers': [{'name': 'X-Api-Key', 'value': 'header-secret', 'secret': True},
                    {'name': 'X-Source', 'value': 'iris', 'secret': False}],
        'auth_type': 'bearer',
        'auth_secret': 'bearer-secret',
        'signing_secret': 'signing-secret',
        'verify_tls': False,
    }
    body.update(overrides)
    return body


class TestsRestWebhooks(TestCase):

    def setUp(self) -> None:
        self._subject = Iris()

    def tearDown(self):
        self._subject.clear_database()

    def _create(self, **overrides):
        return self._subject.create(_WEBHOOKS, _body(**overrides)).json()

    def test_create_webhook_should_return_201(self):
        response = self._subject.create(_WEBHOOKS, _body())
        self.assertEqual(201, response.status_code)

    def test_create_webhook_should_keep_tls_verification_setting(self):
        self.assertFalse(self._create()['verify_tls'])

    def test_create_webhook_should_use_the_proxy_unless_told_otherwise(self):
        self.assertTrue(self._create()['use_proxy'])
        self.assertFalse(self._create(use_proxy=False)['use_proxy'])

    def test_create_webhook_should_never_return_secret_values(self):
        response = self._subject.create(_WEBHOOKS, _body())
        for secret in ('header-secret', 'bearer-secret', 'signing-secret'):
            self.assertNotIn(secret, response.text)
        body = response.json()
        self.assertTrue(body['has_auth_secret'])
        self.assertTrue(body['has_signing_secret'])
        self.assertEqual({'name': 'X-Api-Key', 'value': None, 'secret': True, 'has_value': True}, body['headers'][0])

    def test_create_webhook_should_return_400_without_name(self):
        response = self._subject.create(_WEBHOOKS, _body(name=''))
        self.assertEqual(400, response.status_code)
        self.assertIn('name', response.json()['data'])

    def test_create_webhook_should_return_400_for_an_unknown_event(self):
        response = self._subject.create(_WEBHOOKS, _body(events=['on_postload_nope']))
        self.assertEqual(400, response.status_code)

    def test_create_webhook_should_accept_a_private_destination(self):
        response = self._subject.create(_WEBHOOKS, _body(url='http://10.0.0.1/hook'))
        self.assertEqual(201, response.status_code)

    def test_create_webhook_should_return_400_for_an_invalid_template(self):
        response = self._subject.create(_WEBHOOKS, _body(body_mode='template', body_template='{{ title '))
        self.assertEqual(400, response.status_code)
        self.assertIn('body_template', response.json()['data'])

    def test_create_webhook_should_add_an_activity(self):
        identifier = self._create()['id']
        last_activity = self._subject.get_latest_activity()
        self.assertEqual(f'Webhook #{identifier} "Receiver" created', last_activity['activity_desc'])

    def test_list_webhooks_should_return_created_webhook(self):
        identifier = self._create()['id']
        response = self._subject.get(_WEBHOOKS).json()
        self.assertIn(identifier, [webhook['id'] for webhook in response])

    def test_get_webhook_should_return_404_when_it_does_not_exist(self):
        response = self._subject.get(f'{_WEBHOOKS}/{_IDENTIFIER_FOR_NONEXISTENT_OBJECT}')
        self.assertEqual(404, response.status_code)

    def test_update_webhook_without_secrets_should_keep_them(self):
        webhook = self._create()
        headers = [{'name': 'X-Api-Key', 'value': None, 'secret': True}]
        response = self._subject.update(f'{_WEBHOOKS}/{webhook["id"]}', {'name': 'Renamed', 'headers': headers})
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual('Renamed', body['name'])
        self.assertTrue(body['headers'][0]['has_value'])
        self.assertTrue(body['has_auth_secret'])
        self.assertTrue(body['has_signing_secret'])

    def test_update_webhook_should_clear_a_secret_set_to_null(self):
        webhook = self._create()
        body = self._subject.update(f'{_WEBHOOKS}/{webhook["id"]}', {'signing_secret': None}).json()
        self.assertFalse(body['has_signing_secret'])

    def test_delete_webhook_should_return_204(self):
        webhook = self._create()
        response = self._subject.delete(f'{_WEBHOOKS}/{webhook["id"]}')
        self.assertEqual(204, response.status_code)

    def test_get_webhook_should_return_404_after_delete(self):
        webhook = self._create()
        self._subject.delete(f'{_WEBHOOKS}/{webhook["id"]}')
        response = self._subject.get(f'{_WEBHOOKS}/{webhook["id"]}')
        self.assertEqual(404, response.status_code)

    def test_events_should_list_postload_and_manual_hooks_only(self):
        events = self._subject.get(f'{_WEBHOOKS}/events').json()
        names = [event['name'] for event in events]
        self.assertIn('on_postload_case_create', names)
        self.assertIn('on_postload_notification_create', names)
        self.assertIn('on_manual_trigger_ioc', names)
        self.assertTrue(all(name.startswith(('on_postload_', 'on_manual_trigger_')) for name in names))
        self.assertTrue(all(event['manual'] == event['name'].startswith('on_manual_trigger_') for event in events))

    def test_manual_trigger_should_add_the_webhook_to_the_object_menu(self):
        webhook = self._create(events=['*', 'on_manual_trigger_ioc'], manual_label='Send to SOAR')
        entries = self._subject.get('/api/v2/dim-hooks', query_parameters={'target': 'ioc'}).json()
        entry = next(e for e in entries if e.get('webhook_id') == webhook['id'])
        self.assertEqual('Send to SOAR', entry['manual_hook_ui_name'])
        self.assertEqual('on_manual_trigger_ioc', entry['hook_name'])

    def test_wildcard_should_not_add_the_webhook_to_object_menus(self):
        webhook = self._create(events=['*'])
        entries = self._subject.get('/api/v2/dim-hooks', query_parameters={'target': 'ioc'}).json()
        self.assertNotIn(webhook['id'], [e.get('webhook_id') for e in entries])

    def test_disabled_webhook_should_not_be_in_object_menus(self):
        webhook = self._create(enabled=False, events=['on_manual_trigger_case'])
        entries = self._subject.get('/api/v2/dim-hooks', query_parameters={'target': 'case'}).json()
        self.assertNotIn(webhook['id'], [e.get('webhook_id') for e in entries])

    def test_invoke_manual_trigger_should_record_a_manual_delivery(self):
        webhook = self._create(events=['on_manual_trigger_case'])
        case_identifier = self._subject.create_dummy_case()
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/dim-hooks/invoke', {
            'hook_name': 'on_manual_trigger_case', 'module_name': 'IRIS webhooks', 'hook_ui_name': 'Receiver',
            'type': 'case', 'targets': [case_identifier], 'webhook_id': webhook['id'],
        })
        self.assertEqual(200, response.status_code)
        self.assertEqual(1, response.json()['queued'])
        deliveries = []
        for _ in range(40):
            deliveries = self._subject.get(f'{_WEBHOOKS}/{webhook["id"]}/deliveries').json()['data']
            if deliveries:
                break
            time.sleep(0.5)
        self.assertEqual(['manual'], [d['trigger'] for d in deliveries])
        self.assertEqual('on_manual_trigger_case', deliveries[0]['event'])

    def test_invoke_manual_trigger_should_return_400_for_another_object_type(self):
        webhook = self._create(events=['on_manual_trigger_ioc'])
        case_identifier = self._subject.create_dummy_case()
        response = self._subject.create(f'/api/v2/cases/{case_identifier}/dim-hooks/invoke', {
            'hook_name': 'on_manual_trigger_case', 'type': 'case', 'targets': [case_identifier],
            'webhook_id': webhook['id'],
        })
        self.assertEqual(400, response.status_code)

    def test_preview_should_render_the_template_with_masked_secrets(self):
        body = {'webhook': _body(body_mode='template', body_template='{"text": "{{ title | json_escape }}"}'),
                'event': 'on_postload_case_create'}
        response = self._subject.create(f'{_WEBHOOKS}/preview', body).json()
        self.assertEqual([], response['request']['errors'])
        self.assertIn('Case created', response['request']['body'])
        self.assertNotIn('header-secret', str(response['request']['headers']))
        self.assertNotIn('bearer-secret', str(response['request']['headers']))

    def test_preview_should_report_the_condition_result(self):
        body = {'webhook': _body(condition="object_type == 'alert'"), 'event': 'on_postload_case_create'}
        response = self._subject.create(f'{_WEBHOOKS}/preview', body).json()
        self.assertFalse(response['condition']['matches'])

    def test_test_send_should_reach_a_private_destination(self):
        body = {'webhook': _body(url='http://127.0.0.1:9/hook')}
        response = self._subject.create(f'{_WEBHOOKS}/test', body)
        self.assertEqual(200, response.status_code)

    def test_list_deliveries_should_be_empty_for_a_new_webhook(self):
        webhook = self._create()
        response = self._subject.get(f'{_WEBHOOKS}/{webhook["id"]}/deliveries').json()
        self.assertEqual(0, response['total'])

    def test_disabled_webhook_should_not_record_deliveries(self):
        webhook = self._create(enabled=False)
        self._subject.create_dummy_case()
        response = self._subject.get(f'{_WEBHOOKS}/{webhook["id"]}/deliveries').json()
        self.assertEqual(0, response['total'])

    def test_legacy_module_status_should_return_200(self):
        response = self._subject.get(f'{_WEBHOOKS}/legacy-module')
        self.assertEqual(200, response.status_code)

    def test_list_webhooks_should_return_403_for_a_user_without_server_administrator(self):
        user = self._subject.create_dummy_user()
        response = user.get(_WEBHOOKS)
        self.assertEqual(403, response.status_code)

    def test_create_webhook_should_return_403_for_a_user_without_server_administrator(self):
        user = self._subject.create_dummy_user()
        response = user.create(_WEBHOOKS, _body())
        self.assertEqual(403, response.status_code)
