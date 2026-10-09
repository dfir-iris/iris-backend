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

"""Sending a rendered request: egress checks, redirects, retry classes.
`requests.Session.request` and the egress guard are patched; nothing
leaves the machine.
"""

import threading
import time
from http.server import BaseHTTPRequestHandler
from http.server import HTTPServer
from unittest import TestCase
from unittest.mock import MagicMock
from unittest.mock import patch

import requests

from app.iris_engine.webhooks.render import MASK
from app.iris_engine.webhooks.sender import _PinnedAdapter
from app.iris_engine.webhooks.sender import webhooks_destination_error
from app.iris_engine.webhooks.sender import webhooks_resolve_destination
from app.iris_engine.webhooks.sender import webhooks_send

_SENDER = 'app.iris_engine.webhooks.sender'


def _response(status, headers=None, text='ok', chunks=None):
    response = MagicMock()
    response.status_code = status
    response.headers = headers or {}
    response.reason = 'Reason'
    response.encoding = 'utf-8'
    if chunks is None:
        data = text.encode('utf-8')
        chunks = [data[i:i + 4096] for i in range(0, len(data), 4096)]
    response.iter_content.side_effect = lambda *_args, **_kwargs: iter(chunks)
    return response


def _request(**overrides):
    request = {
        'method': 'POST',
        'url': 'https://hooks.example.org/in',
        'headers': {'Content-Type': 'application/json', 'X-Api-Key': 'k', 'Authorization': 'Bearer t'},
        'log_headers': {'Content-Type': 'application/json', 'X-Api-Key': MASK, 'Authorization': MASK},
        'body': b'{}',
    }
    request.update(overrides)
    return request


class TestWebhooksSend(TestCase):

    def setUp(self):
        egress = patch(f'{_SENDER}.webhooks_resolve_destination', return_value=(None, None))
        self.egress = egress.start()
        self.addCleanup(egress.stop)
        session = patch('requests.Session.request')
        self.send = session.start()
        self.addCleanup(session.stop)

    def test_2xx_should_succeed(self):
        self.send.return_value = _response(204)
        result = webhooks_send(_request())
        self.assertTrue(result['success'])
        self.assertEqual(204, result['status_code'])
        self.assertIsNone(result['error'])

    def test_tls_verification_should_follow_the_setting(self):
        self.send.return_value = _response(200)
        webhooks_send(_request(), verify_tls=False, timeout=7)
        kwargs = self.send.call_args.kwargs
        self.assertFalse(kwargs['verify'])
        self.assertEqual(7, kwargs['timeout'])
        self.assertFalse(kwargs['allow_redirects'])

    def test_5xx_and_429_should_be_retryable(self):
        for status in (429, 500, 503):
            self.send.return_value = _response(status)
            result = webhooks_send(_request())
            self.assertFalse(result['success'])
            self.assertTrue(result['retryable'], status)

    def test_other_4xx_should_not_be_retryable(self):
        self.send.return_value = _response(404)
        result = webhooks_send(_request())
        self.assertFalse(result['retryable'])
        self.assertEqual('HTTP 404 Reason', result['error'])

    def test_refused_destination_should_not_be_sent(self):
        self.egress.return_value = ('private address', None)
        result = webhooks_send(_request())
        self.assertFalse(result['success'])
        self.assertIn('private address', result['error'])
        self.send.assert_not_called()

    def test_timeout_should_be_retryable(self):
        self.send.side_effect = requests.exceptions.Timeout()
        result = webhooks_send(_request(), timeout=3)
        self.assertTrue(result['retryable'])
        self.assertEqual('Timed out after 3s', result['error'])

    def test_certificate_error_should_not_be_retryable_and_explain(self):
        self.send.side_effect = requests.exceptions.SSLError(
            '[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self-signed certificate')
        result = webhooks_send(_request())
        self.assertFalse(result['retryable'])
        self.assertIn('self-signed or private CA', result['error'])

    def test_handshake_error_should_not_blame_certificate_verification(self):
        self.send.side_effect = requests.exceptions.SSLError(
            "SSLEOFError(8, '[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol')")
        result = webhooks_send(_request(), verify_tls=False)
        self.assertTrue(result['retryable'])
        self.assertIn('TLS handshake error', result['error'])
        self.assertNotIn('Disable certificate verification', result['error'])

    def test_redirect_should_not_be_followed_by_default(self):
        self.send.return_value = _response(302, {'Location': 'https://elsewhere.example.org/'})
        result = webhooks_send(_request())
        self.assertEqual(302, result['status_code'])
        self.assertFalse(result['success'])
        self.assertEqual(1, self.send.call_count)

    def test_cross_host_redirect_should_drop_secrets_and_switch_post_to_get(self):
        self.send.side_effect = [_response(302, {'Location': 'https://elsewhere.example.org/x'}), _response(200)]
        result = webhooks_send(_request(), follow_redirects=True)
        self.assertTrue(result['success'])
        self.assertEqual('https://elsewhere.example.org/x', result['final_url'])
        method, url = self.send.call_args.args
        headers = self.send.call_args.kwargs['headers']
        self.assertEqual('GET', method)
        self.assertIsNone(self.send.call_args.kwargs['data'])
        self.assertNotIn('X-Api-Key', headers)
        self.assertNotIn('Authorization', headers)

    def test_same_host_307_should_keep_method_body_and_headers(self):
        self.send.side_effect = [_response(307, {'Location': '/other'}), _response(200)]
        webhooks_send(_request(), follow_redirects=True)
        method, url = self.send.call_args.args
        self.assertEqual(('POST', 'https://hooks.example.org/other'), (method, url))
        self.assertEqual(b'{}', self.send.call_args.kwargs['data'])
        self.assertIn('X-Api-Key', self.send.call_args.kwargs['headers'])

    def test_every_redirect_hop_should_be_egress_checked(self):
        self.send.side_effect = [_response(302, {'Location': 'http://169.254.169.254/'})]
        self.egress.side_effect = [(None, None), ('link-local address', None)]
        result = webhooks_send(_request(), follow_redirects=True)
        self.assertIn('link-local', result['error'])
        self.assertEqual(1, self.send.call_count)

    def test_redirect_loop_should_stop(self):
        self.send.return_value = _response(302, {'Location': '/again'})
        result = webhooks_send(_request(), follow_redirects=True)
        self.assertIn('redirects', result['error'])

    def test_long_response_should_be_truncated(self):
        self.send.return_value = _response(200, text='x' * 50000)
        result = webhooks_send(_request())
        self.assertLess(len(result['response_body']), 20000)
        self.assertIn('truncated', result['response_body'])

    def test_caller_should_be_able_to_keep_a_longer_response(self):
        self.send.return_value = _response(200, text='x' * 50000)
        result = webhooks_send(_request(), max_response_chars=100000)
        self.assertEqual('x' * 50000, result['response_body'])


class TestWebhooksProxy(TestCase):

    def setUp(self):
        egress = patch(f'{_SENDER}.webhooks_resolve_destination', return_value=(None, None))
        egress.start()
        self.addCleanup(egress.stop)
        # autospec: the session comes in as the first argument
        session = patch.object(requests.Session, 'request', autospec=True, return_value=_response(200))
        self.send = session.start()
        self.addCleanup(session.stop)

    def _session(self):
        return self.send.call_args.args[0]

    def test_server_proxies_should_be_used_by_default(self):
        webhooks_send(_request(), proxies={'https': 'http://proxy.example.org:3128'})
        self.assertEqual('http://proxy.example.org:3128', self._session().proxies['https'])
        self.assertTrue(self._session().trust_env)

    def test_use_proxy_off_should_connect_directly(self):
        webhooks_send(_request(), proxies={'https': 'http://proxy.example.org:3128'}, use_proxy=False)
        self.assertEqual({}, self._session().proxies)
        self.assertFalse(self._session().trust_env)

    def test_use_proxy_off_should_keep_the_ca_bundle(self):
        with patch.dict('os.environ', {'REQUESTS_CA_BUNDLE': '/etc/ssl/corp.pem'}):
            webhooks_send(_request(), use_proxy=False)
        self.assertEqual('/etc/ssl/corp.pem', self.send.call_args.kwargs['verify'])

    def test_proxy_refusal_should_say_how_to_bypass_it(self):
        self.send.side_effect = requests.exceptions.ProxyError(
            "Cannot connect to proxy. OSError('Tunnel connection failed: 403 Forbidden')")
        result = webhooks_send(_request())
        self.assertIn('Proxy error', result['error'])
        self.assertIn('Use the proxy', result['error'])


class TestWebhooksPrivateEgress(TestCase):

    def test_private_destination_should_be_allowed_by_default(self):
        with patch(f'{_SENDER}.app') as app:
            app.config = {}
            self.assertIsNone(webhooks_destination_error('http://10.0.0.1/hook'))
            self.assertIsNone(webhooks_destination_error('http://169.254.169.254/latest'))

    def test_private_destination_should_be_refused_when_switched_off(self):
        with patch(f'{_SENDER}.app') as app:
            app.config = {'WEBHOOKS_ALLOW_PRIVATE_EGRESS': False}
            self.assertIn('private', webhooks_destination_error('http://10.0.0.1/hook'))

    def test_report_template_switch_should_not_matter(self):
        with patch(f'{_SENDER}.app') as app:
            app.config = {'WEBHOOKS_ALLOW_PRIVATE_EGRESS': False, 'ALLOW_PRIVATE_EGRESS': True}
            self.assertIsNotNone(webhooks_destination_error('http://127.0.0.1/hook'))


class TestWebhooksStrictEgress(TestCase):
    """With private egress refused, anything not globally routable is
    refused, including IPv4 addresses wrapped in IPv6."""

    def _error(self, url):
        return webhooks_resolve_destination(url, allow_private=False)

    def test_non_global_literals_should_be_refused(self):
        for host in ('100.64.0.1', '192.0.0.8', '198.18.0.1', '127.0.0.1', '169.254.169.254', '0.0.0.0',
                     '224.0.0.1', '[::ffff:127.0.0.1]', '[64:ff9b::a9fe:a9fe]', '[2002:7f00:1::]', '[fd00::1]'):
            error, address = self._error(f'http://{host}/x')
            self.assertIsNotNone(error, host)
            self.assertIsNone(address)

    def test_global_literal_should_be_pinned_to_itself(self):
        self.assertEqual((None, '8.8.8.8'), self._error('https://8.8.8.8/x'))

    def test_host_with_one_private_record_should_be_refused(self):
        with patch(f'{_SENDER}._resolve', return_value=['8.8.8.8', '10.0.0.1']):
            error, _address = self._error('https://mixed.example.org/x')
        self.assertIn('private', error)

    def test_resolved_host_should_return_the_checked_address(self):
        with patch(f'{_SENDER}._resolve', return_value=['8.8.4.4', '8.8.8.8']):
            self.assertEqual((None, '8.8.4.4'), self._error('https://public.example.org/x'))

    def test_private_allowed_should_not_resolve(self):
        with patch(f'{_SENDER}._resolve') as resolve:
            self.assertEqual((None, None), webhooks_resolve_destination('http://10.0.0.1/', allow_private=True))
        resolve.assert_not_called()

    def test_scheme_should_be_checked(self):
        self.assertIn('scheme', webhooks_destination_error('file:///etc/passwd', allow_private=True))


class _Recorder(BaseHTTPRequestHandler):
    hosts = []

    def do_GET(self):
        _Recorder.hosts.append(self.headers.get('Host'))
        payload = b'pinned'
        self.send_response(200)
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


class TestWebhooksPinnedConnection(TestCase):
    """The connection goes to the address that was checked, whatever the
    host name resolves to when the socket opens (DNS rebinding)."""

    def setUp(self):
        self.server = HTTPServer(('127.0.0.1', 0), _Recorder)
        self.port = self.server.server_address[1]
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        _Recorder.hosts = []

    def test_connection_should_use_the_checked_address_and_keep_the_host_header(self):
        url = f'http://rebind.invalid:{self.port}/x'
        with patch(f'{_SENDER}._resolve', return_value=['127.0.0.1']), \
                patch(f'{_SENDER}._address_blocked', return_value=False):
            result = webhooks_send(_request(method='GET', url=url, body=None), use_proxy=False, allow_private=False)
        self.assertTrue(result['success'], result['error'])
        self.assertEqual('pinned', result['response_body'])
        self.assertEqual([f'rebind.invalid:{self.port}'], _Recorder.hosts)

    def test_https_pool_should_verify_against_the_host_name(self):
        adapter = _PinnedAdapter('203.0.113.7')
        pool = adapter.get_connection('https://api.example.org/x')
        self.assertEqual('203.0.113.7', pool.host)
        self.assertEqual('api.example.org', pool.assert_hostname)
        self.assertEqual('api.example.org', pool.conn_kw.get('server_hostname'))


class TestWebhooksResponseLimits(TestCase):

    def setUp(self):
        egress = patch(f'{_SENDER}.webhooks_resolve_destination', return_value=(None, None))
        egress.start()
        self.addCleanup(egress.stop)
        session = patch('requests.Session.request')
        self.send = session.start()
        self.addCleanup(session.stop)

    def test_request_should_be_streamed(self):
        self.send.return_value = _response(200)
        webhooks_send(_request())
        self.assertTrue(self.send.call_args.kwargs['stream'])

    def test_endless_body_should_stop_at_the_byte_cap(self):
        read = []

        def endless():
            while True:
                read.append(1)
                yield b'x' * 16384

        self.send.return_value = _response(200, chunks=endless())
        result = webhooks_send(_request())
        self.assertIn('truncated', result['response_body'])
        self.assertLessEqual(len(read) * 16384, 1024 * 1024 + 16384)

    def test_slow_body_should_hit_the_total_deadline(self):
        def slow():
            while True:
                time.sleep(0.02)
                yield b'x'

        self.send.return_value = _response(200, chunks=slow())
        result = webhooks_send(_request(), deadline=0.1)
        self.assertFalse(result['success'])
        self.assertTrue(result['retryable'])
        self.assertIn('total deadline', result['error'])
