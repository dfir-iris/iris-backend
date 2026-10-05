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

"""Rendering a webhook configuration into a request. Pure, no app context."""

import base64
import hashlib
import hmac
import json
from unittest import TestCase

from app.iris_engine.webhooks.render import MASK
from app.iris_engine.webhooks.render import WebhookRenderError
from app.iris_engine.webhooks.render import webhooks_check_condition
from app.iris_engine.webhooks.render import webhooks_check_template
from app.iris_engine.webhooks.render import webhooks_eval_condition
from app.iris_engine.webhooks.render import webhooks_render_request
from app.iris_engine.webhooks.render import webhooks_render_template
from app.iris_engine.webhooks.render import webhooks_sign
from app.iris_engine.webhooks.render import webhooks_template_context


def _event(**overrides):
    event = {
        'event': 'on_postload_alert_create',
        'title': 'Alert created: "Bad" thing',
        'summary': 'Someone created alert Bad',
        'url': 'https://iris.example.org/alerts/3',
        'case': None,
        'data': {'alert_id': 3, 'alert_title': 'Bad', 'alert_severity': {'severity_name': 'High'}},
    }
    event.update(overrides)
    return event


def _context(**overrides):
    return webhooks_template_context(_event(**overrides), 'abc-123', {'id': 1, 'name': 'Hook'})


def _config(**overrides):
    config = {
        'method': 'POST',
        'url': 'https://hooks.example.org/in',
        'query_params': [],
        'headers': [],
        'auth_type': 'none',
        'body_mode': 'default',
        'content_type': 'application/json',
    }
    config.update(overrides)
    return config


class TestWebhooksTemplates(TestCase):

    def test_context_should_expose_items_as_a_list(self):
        self.assertEqual([_event()['data']], _context()['items'])
        self.assertEqual([1, 2], _context(data=[1, 2])['items'])
        self.assertEqual([], _context(data=None)['items'])

    def test_context_should_expose_the_whole_envelope_as_payload(self):
        context = _context()
        self.assertEqual('abc-123', context['payload']['delivery_id'])
        self.assertEqual({'id': 1, 'name': 'Hook'}, context['payload']['webhook'])

    def test_undefined_lookups_should_render_empty(self):
        self.assertEqual('[]', webhooks_render_template('[{{ data.nothing.deeper }}]', _context()))

    def test_json_escape_should_escape_quotes_and_newlines(self):
        rendered = webhooks_render_template('{"t": "{{ title | json_escape }}\\n{{ x | json_escape }}"}',
                                            dict(_context(), x='a\nb'))
        self.assertEqual('Alert created: "Bad" thing\na\nb', json.loads(rendered)['t'])

    def test_tojson_should_emit_valid_json(self):
        rendered = webhooks_render_template('{{ data | tojson }}', _context())
        self.assertEqual(_event()['data'], json.loads(rendered))

    def test_link_should_follow_the_style(self):
        context = _context()
        self.assertEqual('<https://x|T>', webhooks_render_template("{{ 'https://x' | link('T', 'slack') }}", context))
        self.assertEqual('[T](https://x)', webhooks_render_template("{{ 'https://x' | link('T') }}", context))
        self.assertEqual('<a href="https://x">T</a>',
                         webhooks_render_template("{{ 'https://x' | link('T', 'html') }}", context))
        self.assertEqual('T', webhooks_render_template("{{ none | link('T') }}", context))

    def test_pluck_should_return_a_scalar_for_one_item_and_a_list_otherwise(self):
        single = webhooks_render_template("{{ items | pluck('alert_severity.severity_name') | tojson }}", _context())
        self.assertEqual('"High"', single)
        many = webhooks_render_template("{{ items | pluck('id') | tojson }}", _context(data=[{'id': 1}, {'id': 2}]))
        self.assertEqual('[1, 2]', many)

    def test_sandbox_should_block_python_internals(self):
        with self.assertRaises(WebhookRenderError):
            webhooks_render_template('{{ title.__class__.__mro__[1].__subclasses__() }}', _context())

    def test_check_template_should_report_syntax_errors(self):
        with self.assertRaises(WebhookRenderError) as raised:
            webhooks_check_template('{{ title ', 'body_template')
        self.assertEqual('body_template', raised.exception.field)

    def test_condition_should_accept_braces_or_a_bare_expression(self):
        context = _context()
        self.assertTrue(webhooks_eval_condition("data.alert_severity.severity_name == 'High'", context))
        self.assertTrue(webhooks_eval_condition("{{ data.alert_id > 2 }}", context))
        self.assertFalse(webhooks_eval_condition("data.alert_id > 5", context))

    def test_empty_condition_should_match(self):
        self.assertTrue(webhooks_eval_condition(None, _context()))
        self.assertTrue(webhooks_eval_condition('  ', _context()))

    def test_condition_on_missing_fields_should_be_false_not_an_error(self):
        self.assertFalse(webhooks_eval_condition('data.missing.field == 1', _context()))

    def test_check_condition_should_report_syntax_errors(self):
        with self.assertRaises(WebhookRenderError):
            webhooks_check_condition('data.alert_id ==')


class TestWebhooksRenderRequest(TestCase):

    def test_default_body_should_be_the_envelope(self):
        rendered = webhooks_render_request(_config(), _context())
        body = json.loads(rendered['body'])
        self.assertEqual('on_postload_alert_create', body['event'])
        self.assertEqual('abc-123', body['delivery_id'])
        self.assertEqual('application/json', rendered['headers']['Content-Type'])
        self.assertEqual([], rendered['errors'])

    def test_default_headers_should_identify_the_event(self):
        rendered = webhooks_render_request(_config(), _context(), user_agent='IRIS-Webhooks/3')
        self.assertEqual('IRIS-Webhooks/3', rendered['headers']['User-Agent'])
        self.assertEqual('on_postload_alert_create', rendered['headers']['X-IRIS-Event'])
        self.assertEqual('abc-123', rendered['headers']['X-IRIS-Delivery'])

    def test_template_body_should_be_rendered(self):
        rendered = webhooks_render_request(
            _config(body_mode='template', body_template='{"text": "{{ title | json_escape }}"}'), _context())
        self.assertEqual({'text': 'Alert created: "Bad" thing'}, json.loads(rendered['body']))

    def test_template_body_that_is_not_json_should_be_an_error_for_a_json_content_type(self):
        rendered = webhooks_render_request(
            _config(body_mode='template', body_template='{"text": "{{ title }}"}'), _context())
        self.assertEqual('body_template', rendered['errors'][0]['field'])

    def test_template_body_should_not_be_json_checked_for_other_content_types(self):
        rendered = webhooks_render_request(
            _config(body_mode='template', body_template='text={{ title }}', content_type='text/plain'), _context())
        self.assertEqual([], rendered['errors'])
        self.assertEqual('text/plain', rendered['headers']['Content-Type'])

    def test_no_body_mode_should_send_no_body_and_no_content_type(self):
        rendered = webhooks_render_request(_config(body_mode='none', method='GET'), _context())
        self.assertIsNone(rendered['body'])
        self.assertNotIn('Content-Type', rendered['headers'])
        self.assertEqual('GET', rendered['method'])

    def test_url_and_query_params_should_be_rendered_and_secret_ones_masked_in_the_log(self):
        rendered = webhooks_render_request(_config(
            url='https://hooks.example.org/{{ data.alert_id }}?a=1',
            query_params=[{'name': 'token', 'value': 's3cret', 'secret': True},
                          {'name': 'title', 'value': '{{ data.alert_title }}', 'secret': False}],
        ), _context())
        self.assertEqual('https://hooks.example.org/3?a=1&token=s3cret&title=Bad', rendered['url'])
        self.assertNotIn('s3cret', rendered['log_url'])
        self.assertIn('title=Bad', rendered['log_url'])

    def test_user_headers_should_override_defaults_case_insensitively(self):
        rendered = webhooks_render_request(
            _config(headers=[{'name': 'content-type', 'value': 'application/x-custom', 'secret': False}]), _context())
        self.assertEqual('application/x-custom', rendered['headers']['content-type'])
        self.assertNotIn('Content-Type', rendered['headers'])

    def test_secret_headers_should_be_masked_in_the_log_only(self):
        rendered = webhooks_render_request(
            _config(headers=[{'name': 'X-Api-Key', 'value': 'k', 'secret': True}]), _context())
        self.assertEqual('k', rendered['headers']['X-Api-Key'])
        self.assertEqual(MASK, rendered['log_headers']['X-Api-Key'])

    def test_secret_values_should_be_sent_verbatim(self):
        rendered = webhooks_render_request(
            _config(headers=[{'name': 'X-Api-Key', 'value': 'a{#b{{c', 'secret': True}],
                    query_params=[{'name': 'token', 'value': '{{ title }}', 'secret': True}]),
            _context())
        self.assertEqual([], rendered['errors'])
        self.assertEqual('a{#b{{c', rendered['headers']['X-Api-Key'])
        self.assertIn('token=%7B%7B+title+%7D%7D', rendered['url'])

    def test_header_rendering_a_line_break_should_be_an_error(self):
        rendered = webhooks_render_request(
            _config(headers=[{'name': 'X-Title', 'value': '{{ v }}', 'secret': False}]), dict(_context(), v='a\r\nB: c'))
        self.assertEqual('headers.0', rendered['errors'][0]['field'])
        self.assertNotIn('X-Title', rendered['headers'])

    def test_invalid_header_name_should_be_an_error(self):
        rendered = webhooks_render_request(_config(headers=[{'name': 'Bad Name', 'value': 'x'}]), _context())
        self.assertEqual('headers.0', rendered['errors'][0]['field'])

    def test_basic_auth_should_be_encoded_and_masked(self):
        rendered = webhooks_render_request(
            _config(auth_type='basic', auth_username='bob', auth_secret='pw'), _context())
        expected = f'Basic {base64.b64encode(b"bob:pw").decode()}'
        self.assertEqual(expected, rendered['headers']['Authorization'])
        self.assertEqual(MASK, rendered['log_headers']['Authorization'])

    def test_bearer_auth_should_set_the_header(self):
        rendered = webhooks_render_request(_config(auth_type='bearer', auth_secret='tok'), _context())
        self.assertEqual('Bearer tok', rendered['headers']['Authorization'])

    def test_signature_should_cover_timestamp_and_body(self):
        rendered = webhooks_render_request(_config(), _context(), signing_secret='shh', timestamp=1700000000)
        headers = rendered['headers']
        self.assertEqual('1700000000', headers['X-IRIS-Timestamp'])
        expected = hmac.new(b'shh', b'1700000000.' + rendered['body'], hashlib.sha256).hexdigest()
        self.assertEqual(f'sha256={expected}', headers['X-IRIS-Signature'])
        self.assertEqual(MASK, rendered['log_headers']['X-IRIS-Signature'])

    def test_sign_should_accept_an_empty_body(self):
        self.assertTrue(webhooks_sign('s', '1', None).startswith('sha256='))

    def test_unsafe_attributes_should_render_empty(self):
        self.assertEqual('[]', webhooks_render_template('[{{ title.__class__.__mro__ }}]', _context()))

    def test_render_errors_should_be_collected_not_raised(self):
        rendered = webhooks_render_request(_config(url='https://x/{{ 1 / 0 }}'), _context())
        self.assertEqual('url', rendered['errors'][0]['field'])
