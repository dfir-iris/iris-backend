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

"""The HTTP request node of AI workflows: keystore values only in the
request, a destination data cannot change, the instance's own AI
workflow endpoints refused, dry runs that send nothing, and masked
errors. Runs on the in-memory engine harness."""

import json
from types import SimpleNamespace
from unittest.mock import patch

from app import app
from tests.app.iris_engine.ai_workflows.harness import EngineTestCase
from tests.app.iris_engine.ai_workflows.harness import SECRET_NAME
from tests.app.iris_engine.ai_workflows.harness import SECRET_VALUE
from tests.app.iris_engine.ai_workflows.harness import chain
from tests.app.iris_engine.ai_workflows.harness import workflow

_KEY = f'{{{{ key("{SECRET_NAME}") }}}}'


def _http(**config):
    return {'id': 'http', 'type': 'http_request', 'config': {'use_proxy': False, **config}}


def _vars(**values):
    return {'id': 'vars', 'type': 'set_variables',
            'config': {'variables': [{'name': k, 'value': v} for k, v in values.items()]}}


class _HttpTestCase(EngineTestCase):

    def setUp(self):
        super().setUp()
        self.sent = []
        self.response = {'success': True, 'status_code': 200, 'response_headers': {}, 'response_body': '{}'}
        patcher = patch('app.iris_engine.ai_workflows.nodes.webhooks_send', self._send)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _send(self, request, **kwargs):
        self.sent.append((request, kwargs))
        return dict(self.response)

    def run_nodes(self, *nodes, **kwargs):
        return self.run_to_rest(workflow(chain(*nodes)), **kwargs)

    def http_step(self, run):
        return [s for s in self.steps(run) if s.node_id == 'http'][0]

    def persisted(self, run):
        return json.dumps([run.context, run.error, [(s.input, s.output, s.error) for s in self.steps(run)]],
                          default=str)


class TestsHttpKeys(_HttpTestCase):

    def test_key_should_be_a_placeholder_outside_http_requests(self):
        run = self.run_nodes(_vars(tok=f'Bearer {_KEY}'))
        self.assertEqual({'tok': f'Bearer [secret:{SECRET_NAME}]'}, run.context['vars'])

    def test_key_should_be_sent_but_never_persisted(self):
        run = self.run_nodes(_http(url='https://api.example.org/', auth_type='bearer', auth_secret=_KEY,
                                   body_mode='template', body_template=f'{{"token": "{_KEY}"}}'))
        request, _kwargs = self.sent[0]
        self.assertEqual(f'Bearer {SECRET_VALUE}', request['headers']['Authorization'])
        self.assertNotIn(SECRET_VALUE, self.persisted(run))


class TestsHttpDestination(_HttpTestCase):

    def test_url_values_should_be_percent_encoded(self):
        self.run_nodes(_vars(p='../../admin?x=1#f'), _http(url='https://api.example.org/items/{{ vars.p }}'))
        self.assertEqual('https://api.example.org/items/..%2F..%2Fadmin%3Fx%3D1%23f', self.sent[0][0]['url'])

    def test_templated_host_should_be_refused_at_runtime(self):
        for url in ('https://{{ vars.h }}/x', '{{ vars.u }}', 'https://api.example.org{{ vars.h }}/'):
            self.sent = []
            run = self.run_nodes(_vars(h='evil.example', u='https://evil.example/'), _http(url=url))
            self.assertEqual([], self.sent, url)
            self.assertEqual('failed', run.status, url)

    def test_inbound_hooks_should_be_refused_on_any_host(self):
        for url in ('https://other.example/api/v2/ai-workflows/hooks/abc',
                    'https://other.example/x/..%2Fapi/v2/AI-workflows/hooks/abc',
                    'https://other.example/a/%252e%252e/api/v2/ai-workflows/hooks'):
            self.sent = []
            run = self.run_nodes(_http(url=url))
            self.assertEqual([], self.sent, url)
            self.assertIn('inbound hooks', self.http_step(run).error, url)

    def test_own_callbacks_should_be_refused(self):
        url = 'https://iris.example.org/api/v2/ai-workflows/callbacks/0b5f'
        with patch.dict(app.config, {'AI_WORKFLOWS_CALLBACK_BASE_URL': 'https://IRIS.example.org/'}):
            run = self.run_nodes(_http(url=url))
            self.assertEqual([], self.sent)
            self.assertIn('callbacks of this instance', self.http_step(run).error)
            self.run_nodes(_http(url='https://soar.example.net/api/v2/ai-workflows/callbacks/0b5f'))
        self.assertEqual(1, len(self.sent))


class TestsHttpDryRun(_HttpTestCase):

    def test_dry_run_should_not_send_and_show_the_masked_request(self):
        run = self.run_nodes(_http(url='https://api.example.org/', mode='async', auth_type='bearer',
                                   auth_secret=_KEY, body_mode='template', body_template='{"a": 1}'),
                             dry_run=True)
        self.assertEqual([], self.sent)
        output = self.http_step(run).output
        self.assertTrue(output['dry_run'])
        self.assertEqual('https://api.example.org/', output['request']['url'])
        self.assertEqual('{"a": 1}', output['request']['body'])
        self.assertEqual([], list(self.store.waits.values()))
        self.assertNotIn(SECRET_VALUE, self.persisted(run))


class TestsHttpErrors(_HttpTestCase):

    def test_transport_error_should_be_masked(self):
        self.response = {'success': False, 'status_code': None, 'response_headers': {}, 'response_body': None,
                         'error': f'Connection refused for token {SECRET_VALUE}'}
        run = self.run_nodes(_http(url='https://api.example.org/', auth_type='bearer', auth_secret=_KEY))
        self.assertEqual(1, len(self.sent))
        self.assertNotIn(SECRET_VALUE, self.persisted(run))

    def test_render_error_should_be_masked(self):
        run = self.run_nodes(_http(url='https://api.example.org/', body_mode='template',
                                   body_template=f'{_KEY}{{{{ 9 ** 99999 }}}}'))
        self.assertEqual([], self.sent)
        self.assertNotIn(SECRET_VALUE, self.persisted(run))


class TestsNotifyAudience(EngineTestCase):

    def test_users_audience_without_entity_should_skip_inactive_and_unknown_users(self):
        users = {5: SimpleNamespace(id=5, active=True), 6: SimpleNamespace(id=6, active=False)}
        node = {'id': 'notify', 'type': 'notify', 'config': {'audience': 'users', 'user_ids': [5, 6, 7],
                                                              'title': 'Hello'}}
        with patch('app.iris_engine.ai_workflows.nodes.ai_workflows_db_get_user', users.get), \
                patch('app.iris_engine.notifications.service.notify_many') as notify_many:
            run = self.run_to_rest(workflow(chain(node)), entity_type=None, entity_id=None)
        self.assertEqual('succeeded', run.status)
        self.assertEqual([5], notify_many.call_args.args[0])
