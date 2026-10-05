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

"""Conversion of an iris_webhooks_module configuration. Pure."""

import json
from unittest import TestCase

from app.iris_engine.webhooks.legacy import webhooks_convert_legacy
from app.iris_engine.webhooks.legacy import webhooks_parse_legacy_config
from app.iris_engine.webhooks.render import webhooks_render_template
from app.iris_engine.webhooks.render import webhooks_template_context

_EVENTS = {'on_postload_alert_create', 'on_postload_alert_update', 'on_postload_case_create',
           'on_postload_notification_create', 'on_manual_trigger_case'}


def _context(data, **fields):
    event = {'event': 'on_postload_alert_create', 'title': 'Alert "x"', 'summary': 'S', 'url': 'https://i/a/1',
             'data': data}
    event.update(fields)
    return webhooks_template_context(event)


def _convert(hook):
    return webhooks_convert_legacy({'webhooks': [hook]}, _EVENTS)[0]


class TestWebhooksLegacy(TestCase):

    def test_parse_should_accept_a_json_string_or_a_dict(self):
        self.assertEqual({'webhooks': []}, webhooks_parse_legacy_config('{"webhooks": []}'))
        self.assertEqual({'a': 1}, webhooks_parse_legacy_config({'a': 1}))

    def test_parse_should_reject_invalid_json(self):
        with self.assertRaises(ValueError):
            webhooks_parse_legacy_config('{nope')
        with self.assertRaises(ValueError):
            webhooks_parse_legacy_config('[1]')

    def test_imported_webhooks_should_be_disabled(self):
        converted = _convert({'name': 'A', 'trigger_on': ['all'], 'request_url': 'https://x'})
        self.assertFalse(converted['webhook']['enabled'])
        self.assertEqual(['*'], converted['webhook']['events'])

    def test_rendered_body_should_escape_the_title(self):
        converted = _convert({'name': 'A', 'trigger_on': ['on_postload_alert_create'], 'use_rendering': True,
                              'request_rendering': 'markdown', 'request_body': {'text': '%TITLE%: %DESCRIPTION%'}})
        body = webhooks_render_template(converted['webhook']['body_template'], _context({}))
        self.assertEqual({'text': 'Alert "x": S [https://i/a/1](https://i/a/1)'}, json.loads(body))

    def test_file_placeholder_should_be_dropped_with_a_warning(self):
        converted = _convert({'name': 'A', 'trigger_on': ['all'], 'use_rendering': True,
                              'request_body': {'text': '%TITLE% %FILE%'}})
        self.assertNotIn('%FILE%', converted['webhook']['body_template'])
        self.assertTrue(any('%FILE%' in w for w in converted['warnings']))

    def test_mapped_body_should_reproduce_the_module_output(self):
        converted = _convert({'name': 'A', 'trigger_on': ['all'], 'request_body': {
            'title': 'alerts.alert_title',
            'line': 'Severity ${{alerts.severity.name}}',
            'link': 'object_url',
            'nested': {'id': 'alerts.alert_id'},
        }})
        body = webhooks_render_template(converted['webhook']['body_template'],
                                        _context({'alert_id': 3, 'alert_title': 'T "q"', 'severity': {'name': 'Hi'}}))
        self.assertEqual({'title': 'T "q"', 'line': 'Severity Hi', 'link': 'https://i/a/1', 'nested': {'id': 3}},
                         json.loads(body))

    def test_triggers_should_map_to_postload_events(self):
        converted = _convert({'name': 'A', 'trigger_on': ['all_create', 'on_preload_alert_update',
                                                          'on_manual_trigger_case', 'on_postload_nope']})
        self.assertEqual(['on_postload_alert_create', 'on_postload_case_create', 'on_postload_notification_create',
                          'on_postload_alert_update', 'on_manual_trigger_case'], converted['webhook']['events'])
        warnings = ' '.join(converted['warnings'])
        self.assertIn('on_preload_alert_update replaced by on_postload_alert_update', warnings)
        self.assertIn('Unknown event on_postload_nope', warnings)

    def test_manual_triggers_should_keep_their_menu_label(self):
        converted = _convert({'name': 'A', 'trigger_on': ['all', 'on_manual_trigger_case', 'on_manual_trigger_ioc'],
                              'manual_trigger_name': 'Send to SOAR'})
        self.assertEqual(['*', 'on_manual_trigger_case'], converted['webhook']['events'])
        self.assertEqual('Send to SOAR', converted['webhook']['manual_label'])
        self.assertIn('Unknown manual trigger on_manual_trigger_ioc', ' '.join(converted['warnings']))

    def test_secret_looking_headers_should_be_flagged(self):
        converted = _convert({'name': 'A', 'trigger_on': ['all'],
                              'request_headers': {'Authorization': 'Bearer x', 'X-Source': 'iris'}})
        secrets = {h['name']: h['secret'] for h in converted['webhook']['headers']}
        self.assertEqual({'Authorization': True, 'X-Source': False}, secrets)

    def test_verify_ssl_false_should_disable_tls_verification(self):
        self.assertFalse(_convert({'name': 'A', 'verify_ssl': False})['webhook']['verify_tls'])
        self.assertTrue(_convert({'name': 'A'})['webhook']['verify_tls'])

    def test_hook_without_body_should_use_the_default_payload(self):
        converted = _convert({'name': 'A', 'trigger_on': ['all']})
        self.assertEqual('default', converted['webhook']['body_mode'])
