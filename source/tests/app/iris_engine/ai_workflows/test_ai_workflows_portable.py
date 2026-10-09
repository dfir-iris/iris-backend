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

"""Workflow and block documents: export without secrets, import, and the
authoring guide."""

import time
from unittest import TestCase

from app.iris_engine.ai_workflows.guide import ai_workflows_guide_examples
from app.iris_engine.ai_workflows.guide import ai_workflows_guide_render
from app.iris_engine.ai_workflows.portable import FORMAT_BLOCK
from app.iris_engine.ai_workflows.portable import FORMAT_VERSION
from app.iris_engine.ai_workflows.portable import FORMAT_WORKFLOW
from app.iris_engine.ai_workflows.portable import PortableError
from app.iris_engine.ai_workflows.portable import ai_workflows_portable_export_block
from app.iris_engine.ai_workflows.portable import ai_workflows_portable_export_workflow
from app.iris_engine.ai_workflows.portable import ai_workflows_portable_key_references
from app.iris_engine.ai_workflows.portable import ai_workflows_portable_read_block
from app.iris_engine.ai_workflows.portable import ai_workflows_portable_read_workflow
from app.iris_engine.ai_workflows.portable import ai_workflows_portable_scrub_nodes
from app.iris_engine.ai_workflows.portable import ai_workflows_portable_tools

_TOKEN = 'abcdef0123456789abcdef'


def _http(**config):
    return {'id': 'call', 'type': 'http_request', 'config': {'method': 'GET', 'url': 'https://api.example.org/x',
                                                            **config}}


def _scrubbed(node):
    nodes, warnings = ai_workflows_portable_scrub_nodes([node])
    return nodes[0]['config'], warnings


class TestsPortableScrub(TestCase):

    def test_literal_auth_secret_should_become_a_key_reference(self):
        config, warnings = _scrubbed(_http(auth_type='bearer', auth_secret=_TOKEN))
        self.assertEqual('{{ key("CALL_AUTH_SECRET") }}', config['auth_secret'])
        self.assertEqual([('call', 'auth_secret')], [(w['node_id'], w['field']) for w in warnings])

    def test_key_references_should_be_kept(self):
        node = _http(auth_type='bearer', auth_secret='{{ key("VT") }}',
                     headers=[{'name': 'x-apikey', 'value': '{{ key("VT") }}', 'secret': True}])
        config, warnings = _scrubbed(node)
        self.assertEqual([], warnings)
        self.assertEqual('{{ key("VT") }}', config['headers'][0]['value'])

    def test_credential_header_should_keep_its_scheme(self):
        config, _warnings = _scrubbed(_http(headers=[{'name': 'Authorization', 'value': f'Bearer {_TOKEN}'}]))
        self.assertEqual('Bearer {{ key("CALL_HEADERS_0") }}', config['headers'][0]['value'])

    def test_secret_flagged_header_should_be_replaced_even_when_short(self):
        config, _warnings = _scrubbed(_http(headers=[{'name': 'X-Custom', 'value': 'abc', 'secret': True}]))
        self.assertEqual('{{ key("CALL_HEADERS_0") }}', config['headers'][0]['value'])

    def test_plain_headers_and_query_params_should_be_kept(self):
        node = _http(headers=[{'name': 'Accept', 'value': 'application/json'}],
                     query_params=[{'name': 'limit', 'value': '10'}, {'name': 'api_key', 'value': _TOKEN}])
        config, _warnings = _scrubbed(node)
        self.assertEqual('application/json', config['headers'][0]['value'])
        self.assertEqual('10', config['query_params'][0]['value'])
        self.assertEqual('{{ key("CALL_QUERY_PARAMS_1") }}', config['query_params'][1]['value'])

    def test_url_credentials_should_be_removed(self):
        config, warnings = _scrubbed(_http(url='https://user:pass@api.example.org/x'))
        self.assertEqual('https://api.example.org/x', config['url'])
        self.assertEqual('url', warnings[0]['field'])

    def test_scrub_should_not_modify_the_input(self):
        node = _http(auth_type='bearer', auth_secret=_TOKEN)
        ai_workflows_portable_scrub_nodes([node])
        self.assertEqual(_TOKEN, node['config']['auth_secret'])

    def test_other_nodes_without_secrets_should_be_untouched(self):
        node = {'id': 'py', 'type': 'python', 'config': {'code': 'token = vars["token"]\nresult = token',
                                                         'inputs': [{'name': 'token', 'value': '{{ vars.t }}'}]}}
        self.assertEqual(([node], []), ai_workflows_portable_scrub_nodes([node]))

    def test_literal_in_python_code_should_be_replaced(self):
        node = {'id': 'py', 'type': 'python', 'config': {'code': f'token = "{_TOKEN}"\nresult = 1'}}
        config, warnings = _scrubbed(node)
        self.assertEqual('token = "[secret:PY_CODE]"\nresult = 1', config['code'])
        self.assertEqual(['code'], [w['field'] for w in warnings])

    def test_secrets_anywhere_should_be_replaced(self):
        aws = 'AKIAABCDEFGHIJKLMNOP'
        cases = [
            _http(url=f'https://api.example.org/v1?apikey={_TOKEN}'),
            _http(body_template=f'{{"client_secret": "{_TOKEN}"}}'),
            _http(headers=[{'name': 'Authorization', 'value': f'Bearer {{{{ "{aws}" }}}}'}]),
            _http(auth_type='bearer', auth_secret=f'{{{{ "{_TOKEN}" }}}}'),
            _http(auth_type='bearer', auth_secret=f'{{% set k = "{_TOKEN}" %}}{{{{ k }}}}'),
            _http(query_params=[{'name': 'password', 'value': 'P@ss!w0rd#2024$x'}]),
            _http(headers=[{'name': 'X-Auth-Token', 'value': 'hunter2pass'}]),
            _http(headers=[{'name': 'pwd', 'value': 'hunter2pass'}]),
            _http(url='{{ vars.base }}https://admin:r00tP4ss@api.example.org/'),
            {'id': 'set', 'type': 'set_variables', 'config': {'variables': [{'name': 'x', 'value': aws}]}},
            {'id': 'set', 'type': 'set_variables', 'config': {'variables': [{'name': 'api_token',
                                                                             'value': 'hunter2pass'}]}},
            {'id': 'ai', 'type': 'ai_agent', 'config': {'prompt': f'Use {aws} to log in'}},
            {'id': 'act', 'type': 'action', 'config': {'tool': 't', 'arguments': {'password': 'hunter2pass'}}},
            {'id': 'n', 'type': 'notify', 'label': f'key {aws}', 'config': {}},
            {'id': 'n', 'type': 'notify', 'config': {'unknown': {'nested': ['eyJhbGciOiJIUzI1.eyJzdWIiOiIx.'
                                                                             'SflKxwRJSMeKKF2QT4']}}},
        ]
        for node in cases:
            with self.subTest(node=node):
                nodes, warnings = ai_workflows_portable_scrub_nodes([node])
                text = str(nodes)
                for secret in (_TOKEN, aws, 'P@ss!w0rd', 'hunter2pass', 'r00tP4ss', 'SflKxwRJSMeKKF2QT4'):
                    self.assertNotIn(secret, text)
                self.assertTrue(warnings)

    def test_secret_outside_templates_should_become_a_key_reference(self):
        config, _warnings = _scrubbed(_http(url=f'https://api.example.org/v1?apikey={_TOKEN}&q=1'))
        self.assertEqual('https://api.example.org/v1?apikey={{ key("CALL_URL") }}&q=1', config['url'])

    def test_scan_should_be_linear(self):
        value = '{% raw %}' + '{{' * 200000 + '{% endraw %}' + 'eyJ' * 100000
        started = time.monotonic()
        ai_workflows_portable_scrub_nodes([_http(headers=[{'name': 'Authorization', 'value': value}],
                                                 body_template=value)])
        self.assertLess(time.monotonic() - started, 5)


class TestsPortableExport(TestCase):

    def _definition(self):
        return {
            'name': 'Enrich', 'description': None, 'trigger_type': 'webhook',
            'trigger_config': {'inbound_token': 'tok', 'signing_secret': 'sig', 'entity_type': 'case',
                               'hmac_secret': 'SuperSecretSig99'},
            'graph': {'nodes': [{'id': 'trigger', 'type': 'trigger', 'config': {}},
                                _http(auth_type='bearer', auth_secret=_TOKEN,
                                      headers=[{'name': 'x', 'value': '{{ key("OTHER") }}'}]),
                                {'id': 'act', 'type': 'action', 'config': {'tool': 'iris_case_iocs_update'}}],
                      'edges': []},
            'write_tool_allowlist': ['iris_case_iocs_update'], 'max_runs_per_hour': 10,
            'token_budget_per_run': 0, 'suggestion_audience': 'entity',
            'owner_id': 3, 'customer_scope': [1], 'id': 9,
        }

    def test_workflow_export_should_hold_no_secret_and_no_instance_field(self):
        document = ai_workflows_portable_export_workflow(self._definition(), exported_at='now',
                                                         instance_version='3.0.0')
        self.assertEqual((FORMAT_WORKFLOW, FORMAT_VERSION), (document['format'], document['format_version']))
        self.assertEqual({'entity_type': 'case'}, document['workflow']['trigger_config'])
        self.assertNotIn('owner_id', document['workflow'])
        self.assertNotIn('customer_scope', document['workflow'])
        self.assertNotIn(_TOKEN, str(document))
        self.assertEqual(['CALL_AUTH_SECRET', 'OTHER'], document['requirements']['keystore'])
        self.assertEqual(['iris_case_iocs_update'], document['requirements']['tools'])
        self.assertEqual(1, len(document['warnings']))

    def test_export_should_round_trip_through_import(self):
        document = ai_workflows_portable_export_workflow(self._definition())
        body = ai_workflows_portable_read_workflow(document)
        self.assertEqual(document['workflow'], body)

    def test_block_export_should_round_trip_through_import(self):
        block = {'name': 'Lookup', 'description': 'd', 'category': 'enrichment',
                 'nodes': [_http(auth_type='basic', auth_secret=_TOKEN, auth_username='svc')], 'edges': []}
        document = ai_workflows_portable_export_block(block)
        self.assertEqual(FORMAT_BLOCK, document['format'])
        self.assertNotIn(_TOKEN, str(document))
        self.assertEqual('svc', document['block']['nodes'][0]['config']['auth_username'])
        self.assertEqual(document['block'], ai_workflows_portable_read_block(document))


class TestsPortableRead(TestCase):

    def test_bare_definition_should_be_accepted(self):
        body = ai_workflows_portable_read_workflow({'name': 'x', 'trigger_type': 'manual', 'graph': {},
                                                   'owner_id': 1})
        self.assertEqual({'name': 'x', 'trigger_type': 'manual', 'graph': {}}, body)

    def test_deeply_nested_document_should_be_refused(self):
        graph = {}
        for _ in range(5000):
            graph = {'nodes': graph}
        with self.assertRaises(PortableError):
            ai_workflows_portable_read_workflow({'name': 'x', 'graph': graph})

    def test_wrong_documents_should_be_refused(self):
        for document in ([], {'format': FORMAT_BLOCK, 'format_version': 1, 'block': {}},
                         {'format': FORMAT_WORKFLOW, 'format_version': FORMAT_VERSION + 1, 'workflow': {}},
                         {'format': FORMAT_WORKFLOW, 'format_version': True, 'workflow': {}},
                         {'format': FORMAT_WORKFLOW, 'format_version': 1, 'workflow': []}):
            with self.subTest(document=document), self.assertRaises(PortableError):
                ai_workflows_portable_read_workflow(document)

    def test_requirements_helpers(self):
        self.assertEqual(['A', 'B_2'], ai_workflows_portable_key_references(
            {'x': ['{{ key("B_2") }}', "{{ key('A') }} {{ key(\"A\") }}"]}))
        nodes = [{'type': 'action', 'config': {'tool': 't1'}}, {'type': 'ai_agent', 'config': {'tools': ['t2']}},
                 {'type': 'suggest', 'config': {'proposed_action': {'tool': 't3'}}}, 'junk']
        self.assertEqual(['t0', 't1', 't2', 't3'], ai_workflows_portable_tools(nodes, ['t0']))


class TestsAuthoringGuide(TestCase):

    def test_guide_should_hold_the_catalogue_and_the_examples(self):
        catalogue = {
            'node_types': [{'type': 'python', 'label': 'Python', 'description': 'Run a script.',
                            'ports': ['out', 'error'], 'config_defaults': {'code': ''}}],
            'trigger_types': ['manual', 'event'],
            'hooks': [{'name': 'on_postload_ioc_create', 'description': 'IOC created'}],
            'entity_types': ['case'],
            'suggestion_kinds': ['generic'], 'suggestion_audiences': ['entity'],
            'tools': [{'name': 'iris_case_iocs_update', 'description': 'Update an IOC', 'classification': 'write',
                       'input_schema': {'type': 'object', 'required': ['ioc_identifier'],
                                        'properties': {'ioc_identifier': {'type': 'integer'}}}}],
            'keystore': [{'name': 'VIRUSTOTAL_API_KEY', 'is_secret': True, 'scope': 'global'}],
        }
        markdown = ai_workflows_guide_render(catalogue, ai_workflows_guide_examples())
        for expected in ('`python`', 'on_postload_ioc_create', 'iris_case_iocs_update', '`ioc_identifier`* (integer)',
                         'VIRUSTOTAL_API_KEY', 'virustotal_ioc_enrichment.workflow.json', FORMAT_WORKFLOW):
            self.assertIn(expected, markdown)
