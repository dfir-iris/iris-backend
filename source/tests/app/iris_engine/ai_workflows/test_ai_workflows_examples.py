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

"""The example documents shipped with IRIS (`examples/`): they validate,
carry no secret, and the VirusTotal workflow and block do what their
descriptions say, scripts included."""

import json
import os
from unittest import TestCase
from unittest.mock import patch

from app.iris_engine.ai_workflows.engine import ai_workflows_engine_start_run
from app.iris_engine.ai_workflows.engine import ai_workflows_engine_step
from app.iris_engine.ai_workflows.graph import ai_workflows_graph_validate
from app.iris_engine.ai_workflows.graph import ai_workflows_graph_validate_fragment
from app.iris_engine.ai_workflows.graph import ai_workflows_graph_validate_trigger_config
from app.iris_engine.ai_workflows.guide import ai_workflows_guide_examples
from app.iris_engine.ai_workflows.portable import ai_workflows_portable_key_references
from app.iris_engine.ai_workflows.portable import ai_workflows_portable_literal_secrets
from app.iris_engine.ai_workflows.portable import ai_workflows_portable_read_block
from app.iris_engine.ai_workflows.portable import ai_workflows_portable_read_workflow
from app.iris_engine.ai_workflows.sandbox import ai_workflows_sandbox_run
from tests.app.iris_engine.ai_workflows.harness import OWNER_ID
from tests.app.iris_engine.ai_workflows.harness import EngineTestCase
from tests.app.iris_engine.ai_workflows.harness import FakeResolver
from tests.app.iris_engine.ai_workflows.harness import workflow

_EXAMPLES = os.path.join(os.path.dirname(__file__), '..', '..', '..', '..', 'app', 'iris_engine', 'ai_workflows',
                         'examples')
_NOW = '2026-10-09T10:00:00'
_VT_KEY = 'vt-key-0123456789abcdef0123'
_SHA256 = 'a' * 64
_HOOKS = [('on_postload_ioc_create', ''), ('on_postload_ioc_update', '')]


def _document(name):
    with open(os.path.join(_EXAMPLES, name), encoding='utf-8') as handle:
        return json.load(handle)


def _nodes(document, kind):
    body = document[kind]
    return {n['id']: n for n in (body['graph']['nodes'] if kind == 'workflow' else body['nodes'])}


_WORKFLOW = _document('virustotal_ioc_enrichment.workflow.json')
_BLOCK = _document('virustotal_lookup.block.json')


def _ioc(value, type_name, enrichment=None, tags=''):
    return {'ioc_id': 7, 'ioc_value': value, 'ioc_type': {'type_name': type_name}, 'ioc_enrichment': enrichment,
            'ioc_tags': tags}


def _vt_body(malicious=5, suspicious=0, item_id=_SHA256):
    return {'data': {'id': item_id, 'type': 'file', 'attributes': {
        'last_analysis_stats': {'malicious': malicious, 'suspicious': suspicious, 'harmless': 0, 'undetected': 60},
        'last_analysis_results': {'EngineA': {'category': 'malicious', 'result': 'Trojan.X'},
                                  'EngineB': {'category': 'undetected', 'result': None}},
        'meaningful_name': 'invoice.exe', 'reputation': -40, 'tags': ['peexe'],
        'names': ['invoice.exe', 'payload.bin'], 'last_analysis_date': 1700000000,
        'popular_threat_classification': {'suggested_threat_label': 'trojan.x',
                                          'popular_threat_category': [{'value': 'trojan', 'count': 9},
                                                                      {'value': 'dropper', 'count': 2}]},
    }}}


def _script(node_id, inputs):
    code = _nodes(_WORKFLOW, 'workflow')[node_id]['config']['code']
    outcome = ai_workflows_sandbox_run(code, {'inputs': inputs, 'entity': {}, 'trigger': {}, 'nodes': {},
                                             'vars': {}, 'run': {}})
    if not outcome['ok']:
        raise AssertionError(outcome)
    return outcome['result']


class TestsExampleDocuments(TestCase):

    def test_every_example_should_be_listed(self):
        self.assertEqual({'virustotal_ioc_enrichment.workflow.json', 'virustotal_lookup.block.json'},
                         {e['file'] for e in ai_workflows_guide_examples()})

    @patch('app.iris_engine.ai_workflows.graph.ai_workflows_db_postload_hooks', return_value=_HOOKS)
    def test_workflow_should_validate(self, _hooks):
        body = ai_workflows_portable_read_workflow(_WORKFLOW)
        self.assertEqual([], ai_workflows_graph_validate_trigger_config(body['trigger_type'], body['trigger_config']))
        self.assertEqual([], ai_workflows_graph_validate(body['graph'], body['trigger_type'], body['trigger_config'],
                                                         body['write_tool_allowlist']))

    def test_block_should_validate(self):
        body = ai_workflows_portable_read_block(_BLOCK)
        self.assertEqual([], ai_workflows_graph_validate_fragment({'nodes': body['nodes'], 'edges': body['edges']}))

    def test_examples_should_only_reference_the_keystore(self):
        for document, kind in ((_WORKFLOW, 'workflow'), (_BLOCK, 'block')):
            nodes = list(_nodes(document, kind).values())
            self.assertEqual([], ai_workflows_portable_literal_secrets(nodes))
            self.assertEqual(['VIRUSTOTAL_API_KEY'], ai_workflows_portable_key_references(nodes))
            self.assertEqual(['VIRUSTOTAL_API_KEY'], document['requirements']['keystore'])


class TestsVirusTotalPrepareScript(TestCase):

    def _prepare(self, ioc):
        return _script('prepare', {'ioc': ioc, 'now': _NOW})

    def test_hashes_should_be_looked_up_as_files(self):
        result = self._prepare(_ioc(_SHA256.upper(), 'sha256'))
        self.assertEqual({'collection': 'files', 'id': _SHA256, 'gui': 'file', 'supported': True,
                          'value': _SHA256.upper(), 'kind': 'sha256'}, result)

    def test_composite_types_should_use_the_known_component(self):
        result = self._prepare(_ioc(f'invoice.exe|{_SHA256}', 'filename|sha256'))
        self.assertEqual(('files', _SHA256), (result['collection'], result['id']))

    def test_public_ip_should_be_looked_up_and_private_ip_refused(self):
        self.assertEqual('ip_addresses', self._prepare(_ioc('8.8.8.8', 'ip-dst'))['collection'])
        private = self._prepare(_ioc('10.1.2.3', 'ip-dst'))
        self.assertFalse(private['supported'])
        self.assertIn('Private', private['reason'])

    def test_urls_should_use_the_unpadded_urlsafe_base64_id(self):
        result = self._prepare(_ioc('http://example.org/a', 'url'))
        self.assertEqual(('urls', 'aHR0cDovL2V4YW1wbGUub3JnL2E', 'url'),
                         (result['collection'], result['id'], result['gui']))
        self.assertFalse(self._prepare(_ioc('ftp://example.org/a', 'url'))['supported'])

    def test_domains_should_be_looked_up(self):
        result = self._prepare(_ioc('Evil.Example.ORG.', 'domain'))
        self.assertEqual(('domains', 'evil.example.org'), (result['collection'], result['id']))

    def test_other_values_should_be_skipped_with_a_reason(self):
        result = self._prepare(_ioc('C:\\Windows\\evil.exe', 'filename'))
        self.assertFalse(result['supported'])
        self.assertIn('filename', result['reason'])
        self.assertEqual('Empty IOC value', self._prepare(_ioc('', 'md5'))['reason'])

    def test_value_already_looked_up_today_should_be_skipped(self):
        enrichment = {'virustotal': {'value': _SHA256, 'checked_at': '2026-10-09T08:00:00'}}
        self.assertFalse(self._prepare(_ioc(_SHA256, 'sha256', enrichment))['supported'])
        enrichment['virustotal']['checked_at'] = '2026-10-08T08:00:00'
        self.assertTrue(self._prepare(_ioc(_SHA256, 'sha256', enrichment))['supported'])


class TestsVirusTotalSummaryScript(TestCase):

    _TARGET = {'collection': 'files', 'id': _SHA256, 'gui': 'file', 'supported': True, 'value': _SHA256,
               'kind': 'sha256'}

    def _summary(self, response, ioc=None):
        return _script('summary', {'ioc': ioc or _ioc(_SHA256, 'sha256', {'misp': {'seen': True}}, 'apt, vt:clean'),
                                   'target': self._TARGET, 'response': response, 'now': _NOW})

    def test_malicious_answer_should_be_merged_into_enrichment_and_tags(self):
        result = self._summary({'status_code': 200, 'body': _vt_body(malicious=5)})
        self.assertEqual('malicious', result['verdict'])
        self.assertTrue(result['alert'])
        self.assertEqual('apt,vt:malicious', result['tags'])
        self.assertEqual({'seen': True}, result['enrichment']['misp'])
        vt = result['enrichment']['virustotal']
        self.assertEqual((5, 65, _NOW, 'trojan.x', 'invoice.exe'),
                         (vt['malicious'], vt['engines'], vt['checked_at'], vt['threat_label'], vt['meaningful_name']))
        self.assertEqual(['EngineA: Trojan.X'], vt['detections'])
        self.assertEqual(f'https://www.virustotal.com/gui/file/{_SHA256}', vt['link'])

    def test_summary_should_give_ratio_names_and_classification(self):
        result = self._summary({'status_code': 200, 'body': _vt_body(malicious=5)})
        vt = result['enrichment']['virustotal']
        self.assertEqual(('5/65', ['EngineA'], ['trojan', 'dropper'], ['invoice.exe', 'payload.bin'],
                          '2023-11-14T22:13:20Z', -40),
                         (vt['detection_ratio'], vt['flagged_by'], vt['threat_categories'], vt['names'],
                          vt['last_analysed'], vt['reputation']))
        self.assertEqual(vt['summary'], result['summary'])
        for part in ('Malicious: 5/65 engines', 'trojan.x', 'trojan, dropper', 'invoice.exe, payload.bin',
                     'Flagged by EngineA', 'Reputation: -40', 'Last analysed 2023-11-14'):
            self.assertIn(part, result['summary'])

    def test_engines_without_an_opinion_should_not_count(self):
        body = _vt_body(malicious=5)
        body['data']['attributes']['last_analysis_stats'].update({'type-unsupported': 10, 'timeout': 2})
        self.assertEqual('5/65', self._summary({'status_code': 200, 'body': body})['detection_ratio'])

    def test_low_detection_count_should_be_suspicious_without_alert(self):
        result = self._summary({'status_code': 200, 'body': _vt_body(malicious=1)})
        self.assertEqual(('suspicious', False), (result['verdict'], result['alert']))

    def test_unknown_value_should_be_recorded_as_unknown(self):
        result = self._summary({'status_code': 404, 'body': {'error': {'code': 'NotFoundError'}}})
        self.assertEqual('unknown', result['verdict'])
        self.assertIsNone(result['link'])
        self.assertFalse(result['enrichment']['virustotal']['found'])


class _VirusTotalResolver(FakeResolver):

    def get(self, name):
        if name != 'VIRUSTOTAL_API_KEY':
            raise KeyError(name)
        self.used.add(name)
        return _VT_KEY

    def mask(self, obj):
        if isinstance(obj, str):
            return obj.replace(_VT_KEY, '***')
        if isinstance(obj, dict):
            return {k: self.mask(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.mask(v) for v in obj]
        return obj


def _in_process(source, variables, max_steps=None, timeout_seconds=None):
    return ai_workflows_sandbox_run(source, variables, max_steps=max_steps, timeout_seconds=timeout_seconds)


class _VirusTotalRunTestCase(EngineTestCase):

    def setUp(self):
        super().setUp()
        self.resolver = _VirusTotalResolver()
        self.requests = []
        self.response = {'success': True, 'status_code': 200, 'response_body': json.dumps(_vt_body()),
                         'response_headers': {}}
        self.notified = []

        def _send(request, **_kwargs):
            self.requests.append(request)
            return self.response

        patches = {
            'app.iris_engine.ai_workflows.nodes.ai_workflows_sandbox_execute': _in_process,
            'app.iris_engine.ai_workflows.nodes.webhooks_send': _send,
            'app.iris_engine.ai_workflows.nodes.webhooks_db_proxies': lambda: None,
            'app.iris_engine.ai_workflows.nodes.ai_workflows_db_entity_owner_ids': lambda _type, _id: [OWNER_ID],
            'app.iris_engine.ai_workflows.nodes.ai_workflows_entities_user_can_access': lambda *_args: True,
            'app.iris_engine.notifications.service.notify_many':
                lambda users, *args, **kwargs: self.notified.append((list(users), args, kwargs)),
        }
        for target, replacement in patches.items():
            patcher = patch(target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _drive(self, run):
        for _ in range(20):
            if run.id not in self.enqueued:
                break
            self.enqueued.remove(run.id)
            ai_workflows_engine_step(run.id)
        return run

    def _start(self, wf, ioc):
        payload = {'event': 'on_postload_ioc_create', 'data': ioc, 'case': {'id': 42}}
        run = ai_workflows_engine_start_run(wf, 'event', run_as_user_id=OWNER_ID, entity_type='case', entity_id=42,
                                            sub_entity={'type': 'ioc', 'id': 7}, payload=payload)
        return self._drive(run)


class TestsVirusTotalWorkflowRun(_VirusTotalRunTestCase):

    def _run(self, ioc):
        body = ai_workflows_portable_read_workflow(_WORKFLOW)
        wf = workflow(body['graph'], trigger_type='event', trigger_config=body['trigger_config'],
                      allowlist=body['write_tool_allowlist'])
        self.tool_results['iris_case_iocs_get'] = ioc
        return self._start(wf, ioc)

    def _updates(self):
        return [arguments for tool, arguments, _mode in self.executed if tool == 'iris_case_iocs_update']

    def test_malicious_hash_should_be_enriched_tagged_and_notified(self):
        run = self._run(_ioc(_SHA256, 'sha256', tags='apt'))
        self.assertEqual('succeeded', run.status, run.error)
        self.assertEqual(f'https://www.virustotal.com/api/v3/files/{_SHA256}', self.requests[0]['url'])
        self.assertEqual(_VT_KEY, self.requests[0]['headers']['x-apikey'])
        [update] = self._updates()
        self.assertEqual(7, update['ioc_identifier'])
        self.assertEqual('apt,vt:malicious', update['payload']['ioc_tags'])
        self.assertEqual('malicious', update['payload']['ioc_enrichment']['virustotal']['verdict'])
        [(users, args, kwargs)] = self.notified
        self.assertEqual([OWNER_ID], users)
        self.assertIn('(5/65)', args[1])
        self.assertIn('Malicious: 5/65 engines', kwargs['body'])
        self.assertIn(f'https://www.virustotal.com/gui/file/{_SHA256}', kwargs['body'])
        self.assertNotIn(_VT_KEY, json.dumps(run.context, default=str))
        self.assertNotIn(_VT_KEY, json.dumps([s.input for s in self.steps(run)], default=str))

    def test_private_ip_should_end_without_any_request(self):
        run = self._run(_ioc('192.168.1.10', 'ip-src'))
        self.assertEqual('succeeded', run.status, run.error)
        self.assertEqual([], self.requests)
        self.assertEqual([], self._updates())
        self.assertEqual('nothing_to_do', self.steps(run)[-1].node_id)

    def test_value_unknown_to_virustotal_should_be_recorded_as_unknown(self):
        self.response = {'success': False, 'status_code': 404, 'error': 'HTTP 404',
                         'response_body': '{"error": {"code": "NotFoundError"}}', 'response_headers': {}}
        run = self._run(_ioc(_SHA256, 'sha256'))
        self.assertEqual('succeeded', run.status, run.error)
        [update] = self._updates()
        self.assertEqual('vt:unknown', update['payload']['ioc_tags'])
        self.assertEqual([], self.notified)

    def test_virustotal_failure_should_fail_the_run(self):
        self.response = {'success': False, 'status_code': 429, 'error': 'HTTP 429',
                         'response_body': '{}', 'response_headers': {}}
        run = self._run(_ioc(_SHA256, 'sha256'))
        self.assertEqual('failed', run.status)
        self.assertIn('429', run.error)
        self.assertEqual([], self._updates())


class TestsVirusTotalBlockRun(_VirusTotalRunTestCase):

    def _graph(self):
        body = ai_workflows_portable_read_block(_BLOCK)
        first = body['nodes'][0]['id']
        return {'nodes': [{'id': 'trigger', 'type': 'trigger', 'config': {}}] + body['nodes'],
                'edges': [{'id': 'start', 'source': 'trigger', 'target': first, 'source_port': 'out'}] + body['edges']}

    def test_block_should_give_a_verdict_for_a_hash(self):
        run = self._start(workflow(self._graph()), _ioc(_SHA256, 'sha256'))
        self.assertEqual('succeeded', run.status, run.error)
        self.assertEqual(f'https://www.virustotal.com/api/v3/files/{_SHA256}', self.requests[0]['url'])
        verdict = run.context['nodes'][self.steps(run)[-1].node_id]['output']['result']
        self.assertEqual('malicious', verdict['verdict'])

    def test_block_should_refuse_a_value_that_is_not_a_hash(self):
        run = self._start(workflow(self._graph()), _ioc('8.8.8.8', 'ip-dst'))
        self.assertEqual('failed', run.status)
        self.assertEqual([], self.requests)
