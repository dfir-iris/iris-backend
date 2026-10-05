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

"""Validation and secret round-tripping of webhook writes.

`webhooks_db_hook_names` and the destination guard are patched; the
`Webhook` instances are transient, nothing touches the database.
"""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from app.business.webhooks import _webhooks_validate
from app.business.webhooks import webhooks_invoke_manual
from app.business.webhooks import webhooks_legacy_import
from app.business.webhooks import webhooks_manual_options
from app.business.webhooks import webhooks_public
from app.iris_engine.mail.secrets import decrypt_secret
from app.iris_engine.mail.secrets import encrypt_secret
from app.models.errors import BusinessProcessingError
from app.models.webhooks import Webhook

_BUSINESS = 'app.business.webhooks'
_HOOKS = {'on_postload_alert_create', 'on_postload_case_create', 'on_manual_trigger_ioc'}


def _body(**overrides):
    body = {
        'name': 'Slack',
        'enabled': True,
        'events': ['on_postload_alert_create'],
        'url': 'https://hooks.example.org/in',
    }
    body.update(overrides)
    return body


def _stored(**overrides):
    attributes = {
        'name': 'Stored',
        'enabled': True,
        'events': ['on_postload_alert_create'],
        'method': 'POST',
        'url': 'https://hooks.example.org/in',
        'query_params': [{'name': 'token', 'value': encrypt_secret('q-secret'), 'secret': True}],
        'headers': [{'name': 'X-Api-Key', 'value': encrypt_secret('h-secret'), 'secret': True},
                    {'name': 'X-Source', 'value': 'iris', 'secret': False}],
        'auth_type': 'bearer',
        'auth_secret': encrypt_secret('bearer-token'),
        'signing_secret': encrypt_secret('sig-value'),
        'body_mode': 'default',
        'content_type': 'application/json',
        'verify_tls': True,
        'timeout_seconds': 10,
        'max_retries': 3,
        'follow_redirects': False,
        'use_proxy': True,
    }
    attributes.update(overrides)
    return Webhook(**attributes)


class TestWebhooksValidate(TestCase):

    def setUp(self):
        for target, value in (('webhooks_db_hook_names', _HOOKS), ('webhooks_destination_error', None)):
            patcher = patch(f'{_BUSINESS}.{target}', return_value=value)
            setattr(self, target, patcher.start())
            self.addCleanup(patcher.stop)

    def _errors(self, body, **kwargs):
        with self.assertRaises(BusinessProcessingError) as raised:
            _webhooks_validate(body, **kwargs)
        return raised.exception.get_data()

    def test_minimal_body_should_get_defaults(self):
        attributes = _webhooks_validate(_body())
        self.assertEqual('POST', attributes['method'])
        self.assertEqual('default', attributes['body_mode'])
        self.assertTrue(attributes['verify_tls'])
        self.assertEqual(10, attributes['timeout_seconds'])
        self.assertEqual(3, attributes['max_retries'])
        self.assertFalse(attributes['follow_redirects'])
        self.assertTrue(attributes['use_proxy'])

    def test_tls_verification_can_be_disabled(self):
        self.assertFalse(_webhooks_validate(_body(verify_tls=False))['verify_tls'])

    def test_proxy_can_be_bypassed(self):
        self.assertFalse(_webhooks_validate(_body(use_proxy=False))['use_proxy'])

    def test_missing_name_and_url_should_be_reported_together(self):
        errors = self._errors(_body(name=' ', url=''))
        self.assertIn('name', errors)
        self.assertIn('url', errors)

    def test_unknown_event_should_be_refused(self):
        self.assertIn('events', self._errors(_body(events=['on_postload_nope'])))

    def test_wildcard_should_keep_manual_triggers(self):
        attributes = _webhooks_validate(_body(events=['on_postload_alert_create', '*', 'on_manual_trigger_ioc']))
        self.assertEqual(['*', 'on_manual_trigger_ioc'], attributes['events'])

    def test_unknown_manual_trigger_should_be_refused_even_with_the_wildcard(self):
        self.assertIn('events', self._errors(_body(events=['*', 'on_manual_trigger_nope'])))

    def test_manual_label_should_be_trimmed_and_optional(self):
        self.assertEqual('Send to SOAR', _webhooks_validate(_body(manual_label=' Send to SOAR '))['manual_label'])
        self.assertIsNone(_webhooks_validate(_body(manual_label='  '))['manual_label'])
        self.assertIn('manual_label', self._errors(_body(manual_label='x' * 256)))

    def test_enabled_without_events_should_be_refused_but_disabled_is_fine(self):
        self.assertIn('events', self._errors(_body(events=[])))
        self.assertEqual([], _webhooks_validate(_body(events=[], enabled=False))['events'])

    def test_wildcard_should_absorb_the_other_events(self):
        self.assertEqual(['*'], _webhooks_validate(_body(events=['on_postload_alert_create', '*']))['events'])

    def test_bad_condition_and_template_should_be_refused(self):
        errors = self._errors(_body(condition='data.x ==', body_mode='template', body_template='{{ x '))
        self.assertIn('condition', errors)
        self.assertIn('body_template', errors)

    def test_template_mode_needs_a_template(self):
        self.assertIn('body_template', self._errors(_body(body_mode='template', body_template='  ')))

    def test_bounds_should_be_enforced(self):
        errors = self._errors(_body(timeout_seconds=0, max_retries=11, method='TRACE'))
        self.assertEqual({'timeout_seconds', 'max_retries', 'method'}, set(errors))

    def test_refused_literal_destination_should_be_reported(self):
        self.webhooks_destination_error.return_value = 'private address'
        self.assertEqual(['private address'], self._errors(_body())['url'])

    def test_templated_url_should_skip_the_destination_check_but_keep_the_scheme(self):
        _webhooks_validate(_body(url='https://hooks.example.org/{{ case.id }}'))
        self.webhooks_destination_error.assert_not_called()
        self.assertIn('url', self._errors(_body(url='{{ url }}')))

    def test_invalid_header_name_should_be_refused(self):
        self.assertIn('headers', self._errors(_body(headers=[{'name': 'Bad: name', 'value': 'x'}])))

    def test_basic_auth_needs_a_username_and_bearer_a_token(self):
        self.assertIn('auth_username', self._errors(_body(auth_type='basic', auth_secret='pw')))
        self.assertIn('auth_secret', self._errors(_body(auth_type='bearer')))

    def test_new_secrets_should_be_encrypted(self):
        attributes = _webhooks_validate(_body(
            headers=[{'name': 'X-Api-Key', 'value': 'k', 'secret': True}],
            auth_type='bearer', auth_secret='tok', signing_secret='sig'))
        self.assertNotEqual('k', attributes['headers'][0]['value'])
        self.assertEqual('k', decrypt_secret(attributes['headers'][0]['value']))
        self.assertEqual('tok', decrypt_secret(attributes['auth_secret']))
        self.assertEqual('sig', decrypt_secret(attributes['signing_secret']))

    def test_new_secret_without_a_value_should_be_refused(self):
        self.assertIn('headers', self._errors(_body(headers=[{'name': 'X-Api-Key', 'value': None, 'secret': True}])))

    def test_update_without_secret_values_should_keep_the_stored_ones(self):
        existing = _stored()
        attributes = _webhooks_validate({
            'name': 'Renamed',
            'query_params': [{'name': 'token', 'value': None, 'secret': True}],
            'headers': [{'name': 'x-api-key', 'value': None, 'secret': True}],
        }, existing=existing)
        self.assertEqual('Renamed', attributes['name'])
        self.assertEqual('q-secret', decrypt_secret(attributes['query_params'][0]['value']))
        self.assertEqual('h-secret', decrypt_secret(attributes['headers'][0]['value']))
        self.assertEqual('bearer-token', decrypt_secret(attributes['auth_secret']))
        self.assertEqual('sig-value', decrypt_secret(attributes['signing_secret']))

    def test_update_should_keep_fields_left_out(self):
        attributes = _webhooks_validate({'name': 'Renamed'}, existing=_stored(verify_tls=False, max_retries=0))
        self.assertFalse(attributes['verify_tls'])
        self.assertEqual(0, attributes['max_retries'])
        self.assertEqual(2, len(attributes['headers']))

    def test_explicit_null_should_clear_a_secret(self):
        attributes = _webhooks_validate({'signing_secret': None}, existing=_stored())
        self.assertIsNone(attributes['signing_secret'])

    def test_switching_auth_off_should_drop_the_secret(self):
        attributes = _webhooks_validate({'auth_type': 'none'}, existing=_stored())
        self.assertIsNone(attributes['auth_secret'])

    def test_preview_mode_should_not_require_name_events_or_destination(self):
        attributes = _webhooks_validate({'url': 'https://10.0.0.1/'}, strict=False)
        self.assertEqual('Untitled webhook', attributes['name'])
        self.webhooks_destination_error.assert_not_called()


class TestWebhooksPublic(TestCase):

    def test_public_view_should_never_return_secret_values(self):
        webhook = _stored()
        webhook.id = 1
        webhook.created_by = SimpleNamespace(id=1, name='Admin')
        public = webhooks_public(webhook)
        flat = repr(public)
        for secret in ('q-secret', 'h-secret', 'bearer-token', 'sig-value'):
            self.assertNotIn(secret, flat)
        self.assertNotIn(webhook.auth_secret, flat)
        self.assertEqual({'name': 'X-Api-Key', 'value': None, 'secret': True, 'has_value': True},
                         public['headers'][0])
        self.assertEqual('iris', public['headers'][1]['value'])
        self.assertTrue(public['has_auth_secret'])
        self.assertTrue(public['has_signing_secret'])
        self.assertNotIn('auth_secret', public)


class TestWebhooksLegacyImport(TestCase):

    def setUp(self):
        converted = [{'webhook': _body(name=name), 'warnings': []} for name in ('Existing', 'Fresh', 'Fresh')]
        patches = {
            'webhooks_db_get_module': SimpleNamespace(id=1),
            '_legacy_module_config': {},
            'webhooks_parse_legacy_config': {},
            'webhooks_convert_legacy': converted,
            'webhooks_db_list': [_stored(name='Existing')],
            'webhooks_db_hook_names': _HOOKS,
            'webhooks_destination_error': None,
            '_instance_url': '',
            'track_activity': None,
        }
        for target, value in patches.items():
            patcher = patch(f'{_BUSINESS}.{target}', return_value=value)
            setattr(self, target, patcher.start())
            self.addCleanup(patcher.stop)
        patcher = patch(f'{_BUSINESS}.db_create')
        self.db_create = patcher.start()
        self.addCleanup(patcher.stop)

    def test_names_already_present_should_be_skipped(self):
        result = webhooks_legacy_import(1)
        self.assertEqual(['Fresh'], [item['name'] for item in result['created']])
        self.assertEqual(['Existing', 'Fresh'], [item['name'] for item in result['skipped']])
        self.assertIn('name', result['skipped'][0]['errors'])
        self.assertEqual(1, self.db_create.call_count)


class TestWebhooksManualTriggers(TestCase):

    def setUp(self):
        self.webhooks = [
            _stored(name='SOAR', manual_label='Send to SOAR', events=['*', 'on_manual_trigger_ioc']),
            _stored(name='Plain', manual_label=None, events=['on_manual_trigger_ioc']),
            _stored(name='Automatic only', events=['*']),
        ]
        for index, webhook in enumerate(self.webhooks, start=1):
            webhook.id = index
        for target, value in (
                ('webhooks_db_list_enabled', self.webhooks),
                ('webhooks_current_actor', {'id': 1, 'login': 'adm', 'name': 'Adm'}),
        ):
            patcher = patch(f'{_BUSINESS}.{target}', return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch(f'{_BUSINESS}.webhooks_db_get', side_effect=lambda i: self.webhooks[i - 1])
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch(f'{_BUSINESS}.webhooks_build_event', side_effect=lambda hook, data, **_: {'event': hook,
                                                                                                    'data': data})
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch('app.iris_engine.webhooks.tasks.webhooks_dispatch_task')
        self.dispatch = patcher.start()
        self.addCleanup(patcher.stop)

    def test_options_should_list_only_explicit_subscriptions_with_their_label(self):
        options = webhooks_manual_options('ioc')
        self.assertEqual(['Send to SOAR', 'Plain'], [o['manual_hook_ui_name'] for o in options])
        self.assertEqual([1, 2], [o['webhook_id'] for o in options])
        self.assertTrue(all(o['hook_name'] == 'on_manual_trigger_ioc' for o in options))
        self.assertEqual([], webhooks_manual_options('case'))

    def test_invoke_should_queue_one_manual_delivery_per_target(self):
        self.assertEqual(2, webhooks_invoke_manual(1, 'on_manual_trigger_ioc', ['a', 'b'], caseid=3))
        calls = self.dispatch.delay.call_args_list
        self.assertEqual(2, len(calls))
        self.assertEqual(({'event': 'on_manual_trigger_ioc', 'data': 'a'}, [1], 'manual'), calls[0].args)

    def test_invoke_should_refuse_a_hook_the_webhook_is_not_subscribed_to(self):
        for webhook_id, hook in ((3, 'on_manual_trigger_ioc'), (1, 'on_manual_trigger_case'),
                                 (1, 'on_postload_alert_create'), ('x', 'on_manual_trigger_ioc')):
            with self.assertRaises(BusinessProcessingError):
                webhooks_invoke_manual(webhook_id, hook, ['a'])
        self.dispatch.delay.assert_not_called()

    def test_invoke_should_refuse_a_disabled_webhook(self):
        self.webhooks[0].enabled = False
        with self.assertRaises(BusinessProcessingError):
            webhooks_invoke_manual(1, 'on_manual_trigger_ioc', ['a'])
