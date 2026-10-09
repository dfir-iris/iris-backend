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

"""The IOC hunt example: it validates, its scripts build the SIEM search
(Elasticsearch or Splunk) and sort the hosts it answers into known and
new, and a run suggests adding each new host to the case and tells the
case owner."""

import json
import os
from urllib.parse import parse_qs
from unittest import TestCase
from unittest.mock import patch

from app.iris_engine.ai_workflows.engine import ai_workflows_engine_expire_wait
from app.iris_engine.ai_workflows.engine import ai_workflows_engine_start_run
from app.iris_engine.ai_workflows.engine import ai_workflows_engine_step
from app.iris_engine.ai_workflows.graph import ai_workflows_graph_validate
from app.iris_engine.ai_workflows.graph import ai_workflows_graph_validate_trigger_config
from app.iris_engine.ai_workflows.portable import ai_workflows_portable_key_references
from app.iris_engine.ai_workflows.portable import ai_workflows_portable_literal_secrets
from app.iris_engine.ai_workflows.portable import ai_workflows_portable_read_workflow
from app.iris_engine.ai_workflows.portable import ai_workflows_portable_tools
from app.iris_engine.ai_workflows.sandbox import ai_workflows_sandbox_run
from tests.app.iris_engine.ai_workflows.harness import OWNER_ID
from tests.app.iris_engine.ai_workflows.harness import EngineTestCase
from tests.app.iris_engine.ai_workflows.harness import FakeResolver
from tests.app.iris_engine.ai_workflows.harness import workflow

_PATH = os.path.join(os.path.dirname(__file__), '..', '..', '..', '..', 'app', 'iris_engine', 'ai_workflows',
                     'examples', 'ioc_estate_hunt.workflow.json')
with open(_PATH, encoding='utf-8') as _handle:
    _DOCUMENT = json.load(_handle)
_NODES = {n['id']: n for n in _DOCUMENT['workflow']['graph']['nodes']}
_SIEM_KEY = 'siem-key-0123456789abcdef'
_SHA256 = 'b' * 64
_TYPES = [{'id': 1, 'name': 'Windows - Computer'}, {'id': 3, 'name': 'Linux - Computer'},
          {'id': 9, 'name': 'Mac - Computer'}]


def _script(node_id, inputs):
    outcome = ai_workflows_sandbox_run(_NODES[node_id]['config']['code'], {'inputs': inputs},
                                       max_steps=_NODES[node_id]['config']['max_steps'])
    if not outcome['ok']:
        raise AssertionError(outcome)
    return outcome['result']


def _ioc(value, type_name):
    return {'ioc_id': 7, 'ioc_value': value, 'ioc_type': {'type_name': type_name}}


def _bucket(host, hits, os_type=None, ip=None, last_seen=None):
    return {'key': host, 'doc_count': hits,
            'os': {'buckets': [{'key': os_type, 'doc_count': hits}] if os_type else []},
            'ip': {'buckets': [{'key': ip, 'doc_count': hits}] if ip else []},
            'last_seen': {'value': 1, 'value_as_string': last_seen} if last_seen else {'value': None}}


def _answer(*buckets, total=None):
    return {'took': 12, 'hits': {'total': {'value': total if total is not None else sum(b['doc_count'] for b in buckets),
                                           'relation': 'eq'}, 'hits': []},
            'aggregations': {'hosts': {'buckets': list(buckets)}}}


class TestsIocHuntDocument(TestCase):

    @patch('app.iris_engine.ai_workflows.graph.ai_workflows_db_postload_hooks',
           return_value=[('on_postload_ioc_create', '')])
    def test_workflow_should_validate(self, _hooks):
        body = ai_workflows_portable_read_workflow(_DOCUMENT)
        self.assertEqual([], ai_workflows_graph_validate_trigger_config(body['trigger_type'], body['trigger_config']))
        self.assertEqual([], ai_workflows_graph_validate(body['graph'], body['trigger_type'], body['trigger_config'],
                                                         body['write_tool_allowlist']))

    def test_workflow_should_only_suggest_writes(self):
        self.assertEqual([], _DOCUMENT['workflow']['write_tool_allowlist'])

    def test_requirements_should_match_the_graph(self):
        nodes = list(_NODES.values())
        self.assertEqual([], ai_workflows_portable_literal_secrets(nodes))
        self.assertEqual(['SIEM_API_KEY'], ai_workflows_portable_key_references(nodes))
        self.assertEqual({'keystore': ['SIEM_API_KEY'], 'tools': ai_workflows_portable_tools(nodes, [])},
                         _DOCUMENT['requirements'])


def _splunk(*rows):
    return {'preview': False, 'init_offset': 0, 'messages': [], 'fields': [{'name': 'host_name'}],
            'results': [{'host_name': host, 'hits': str(hits), 'os': os_name, 'last_seen': last_seen}
                        for host, hits, os_name, last_seen in rows]}


def _splunk_document():
    document = json.loads(json.dumps(_DOCUMENT))
    for node in document['workflow']['graph']['nodes']:
        if node['id'] == 'prepare':
            node['config']['inputs'][0]['value'] = 'splunk'
    return document


class TestsIocHuntPrepareScript(TestCase):

    def _prepare(self, ioc, siem='elastic'):
        return _script('prepare', {'siem': siem, 'ioc': ioc, 'days': 30})

    def _terms(self, result):
        return result['body']['query']['bool']['filter'][1]['bool']['should']

    def test_ip_should_be_searched_in_the_network_fields(self):
        result = self._prepare(_ioc('10.1.2.3', 'ip-dst'))
        self.assertEqual(('ip', '10.1.2.3'), (result['kind'], result['value']))
        self.assertEqual([{'term': {'source.ip': '10.1.2.3'}}, {'term': {'destination.ip': '10.1.2.3'}},
                          {'term': {'client.ip': '10.1.2.3'}}, {'term': {'server.ip': '10.1.2.3'}}],
                         self._terms(result))

    def test_search_should_count_hits_per_host_over_the_window(self):
        body = self._prepare(_ioc('evil.example.org', 'domain'))['body']
        self.assertEqual((0, True), (body['size'], body['track_total_hits']))
        self.assertEqual({'range': {'@timestamp': {'gte': 'now-30d'}}}, body['query']['bool']['filter'][0])
        self.assertEqual('host.name', body['aggs']['hosts']['terms']['field'])
        self.assertEqual({'os', 'ip', 'last_seen'}, set(body['aggs']['hosts']['aggs']))

    def test_domains_and_hashes_should_be_normalised(self):
        domain = self._prepare(_ioc('Evil.Example.ORG.', 'domain'))
        self.assertEqual({'term': {'dns.question.name': 'evil.example.org'}}, self._terms(domain)[0])
        digest = self._prepare(_ioc(_SHA256.upper(), 'sha256'))
        self.assertEqual({'term': {'file.hash.sha256': _SHA256}}, self._terms(digest)[0])

    def test_composite_types_should_use_the_most_specific_component(self):
        result = self._prepare(_ioc(f'invoice.exe|{_SHA256}', 'filename|sha256'))
        self.assertEqual(('sha256', _SHA256), (result['kind'], result['value']))

    def test_unsupported_values_should_be_skipped_with_a_reason(self):
        for ioc, reason in ((_ioc('HKLM\\Run', 'regkey'), 'No search for IOCs of type regkey'),
                            (_ioc('not-an-ip', 'ip-src'), 'not-an-ip is not an IP address'),
                            (_ioc('', 'md5'), 'Empty IOC value')):
            result = self._prepare(ioc)
            self.assertEqual((False, reason), (result['supported'], result['reason']))

    def test_elastic_should_be_the_default(self):
        self.assertEqual('elastic', _NODES['prepare']['config']['inputs'][0]['value'])
        result = _script('prepare', {'ioc': _ioc('10.1.2.3', 'ip-dst'), 'days': 30})
        self.assertEqual(('elastic', False), (result['siem'], 'form' in result))

    def test_splunk_should_get_a_oneshot_search_over_the_cim_fields(self):
        result = self._prepare(_ioc('Evil.Example.ORG', 'domain'), siem='Splunk')
        self.assertEqual(('splunk', False), (result['siem'], 'body' in result))
        self.assertEqual({'search': 'search index=* (query="evil.example.org" OR url_domain="evil.example.org" OR '
                                    'dest_host="evil.example.org") | stats count AS hits, max(_time) AS last_seen, '
                                    'latest(os) AS os BY host | rename host AS host_name | eval last_seen=strftime('
                                    'last_seen, "%Y-%m-%dT%H:%M:%S%z") | sort 200 - hits',
                          'exec_mode': 'oneshot', 'output_mode': 'json', 'count': 0, 'earliest_time': '-30d',
                          'latest_time': 'now'}, result['form'])

    def test_splunk_values_should_not_escape_their_quotes(self):
        search = self._prepare(_ioc('a" OR index=_internal "b\\', 'filename'), siem='splunk')['form']['search']
        self.assertIn('(file_name="a\\" OR index=_internal \\"b\\\\" OR ', search)

    def test_control_characters_should_not_be_searched(self):
        result = self._prepare(_ioc('a\nb', 'filename'), siem='splunk')
        self.assertEqual((False, 'The IOC value holds control characters'), (result['supported'], result['reason']))

    def test_unknown_siem_should_fail(self):
        outcome = ai_workflows_sandbox_run(_NODES['prepare']['config']['code'], {'inputs': {
            'siem': 'qradar', 'ioc': _ioc('10.1.2.3', 'ip')}})
        self.assertEqual(('failed', 'Unknown SIEM qradar: set the input siem to elastic or splunk'),
                         (outcome['kind'], outcome['error']))


class TestsIocHuntHitsScript(TestCase):

    _ASSETS = [{'asset_name': 'WS-001', 'asset_ip': '10.0.0.1'},
               {'asset_name': 'printer', 'asset_ip': '10.0.0.7, 10.0.0.8'}]

    def _hits(self, response, assets=None, max_suggestions=5, splunk=None):
        return _script('hits', {'response': response, 'splunk': splunk,
                                'assets': self._ASSETS if assets is None else assets,
                                'max_suggestions': max_suggestions})

    def test_hosts_of_the_case_should_be_told_apart_by_name_short_name_or_ip(self):
        result = self._hits(_answer(_bucket('ws-001.corp.local', 30, 'windows', '10.0.0.5'),
                                    _bucket('srv-db', 10, 'linux', '10.0.0.9', '2026-10-08T10:00:00.000Z'),
                                    _bucket('mac-7', 2, None, '10.0.0.7'), total=50))
        self.assertEqual((50, 3, ['ws-001.corp.local', 'mac-7'], 1, True),
                         (result['total_hits'], result['hosts'], result['known_hosts'], result['new_count'],
                          result['has_new']))
        [host] = result['to_suggest']
        self.assertEqual(('srv-db', 10, 'linux', '10.0.0.9', '2026-10-08T10:00:00.000Z'),
                         (host['host'], host['hits'], host['os'], host['ip'], host['last_seen']))
        self.assertIn('| srv-db | 10 | linux | 2026-10-08T10:00:00.000Z |', result['table'])

    def test_suggestions_should_be_capped_most_hits_first(self):
        result = self._hits(_answer(*[_bucket(f'host-{i}', i) for i in range(1, 9)]), assets=[],
                            max_suggestions=3)
        self.assertEqual(8, result['new_count'])
        self.assertEqual(['host-8', 'host-7', 'host-6'], [h['host'] for h in result['to_suggest']])

    def test_events_without_aggregation_should_be_counted_per_host(self):
        response = {'hits': {'total': 3, 'hits': [
            {'_source': {'@timestamp': '2026-10-08T10:00:00Z', 'host': {'name': 'lnx-1', 'os': {'type': 'linux'}}}},
            {'_source': {'@timestamp': '2026-10-08T09:00:00Z', 'host': {'name': 'lnx-1'}}},
            {'_source': {'@timestamp': '2026-10-08T08:00:00Z'}}]}}
        result = self._hits(response, assets=[])
        self.assertEqual([('lnx-1', 2, 'linux')], [(h['host'], h['hits'], h['os']) for h in result['new_hosts']])
        self.assertEqual(3, result['total_hits'])

    def test_splunk_rows_should_be_read_as_hosts(self):
        result = self._hits(None, splunk=_splunk(('WS-001', 30, 'Microsoft Windows 10', None),
                                                 ('srv-db', 12, None, '2026-10-08T10:00:00+0000'),
                                                 ('', 3, None, None)))
        self.assertEqual((42, 2, ['WS-001']), (result['total_hits'], result['hosts'], result['known_hosts']))
        [host] = result['to_suggest']
        self.assertEqual(('srv-db', 12, None, None, '2026-10-08T10:00:00+0000'),
                         (host['host'], host['hits'], host['os'], host['ip'], host['last_seen']))

    def test_no_hit_should_have_nothing_new(self):
        result = self._hits(_answer())
        self.assertEqual((0, 0, False, ''), (result['total_hits'], result['hosts'], result['has_new'], result['table']))


class TestsIocHuntPickScript(TestCase):

    _HOSTS = [{'host': 'srv-db', 'hits': 10, 'os': 'linux', 'ip': '10.0.0.9', 'last_seen': None},
              {'host': 'kiosk', 'hits': 1, 'os': 'solaris', 'ip': None, 'last_seen': '2026-10-08'}]

    def _pick(self, index, types=None, default_type='Windows - Computer'):
        return _script('pick', {'hosts': self._HOSTS, 'types': _TYPES if types is None else types,
                                'ioc': {'value': 'evil.example.org', 'days': 30}, 'index': index,
                                'default_type': default_type})

    def test_host_should_become_an_asset_of_the_type_of_its_os(self):
        result = self._pick(0)
        self.assertEqual({'asset_name': 'srv-db', 'asset_type_id': 3, 'asset_ip': '10.0.0.9', 'asset_tags': 'ioc-hunt',
                          'asset_description': 'Seen with IOC evil.example.org in 10 SIEM event(s) over the last '
                                               '30 days (last seen unknown).'}, result['payload'])
        self.assertEqual((1, True), (result['next'], result['more']))

    def test_os_names_should_be_matched_by_their_family(self):
        hosts = [{'host': 'ws', 'hits': 1, 'os': 'Microsoft Windows 10 Pro'}, {'host': 'mb', 'hits': 1, 'os': 'macOS'}]
        types = [_script('pick', {'hosts': hosts, 'types': _TYPES, 'ioc': {}, 'index': index,
                                  'default_type': 'Linux - Computer'})['payload']['asset_type_id']
                 for index in (0, 1)]
        self.assertEqual([1, 9], types)

    def test_unknown_os_should_use_the_default_type(self):
        result = self._pick(1)
        self.assertEqual((1, False), (result['payload']['asset_type_id'], result['more']))
        self.assertNotIn('asset_ip', result['payload'])

    def test_missing_asset_type_should_fail(self):
        outcome = ai_workflows_sandbox_run(_NODES['pick']['config']['code'], {'inputs': {
            'hosts': self._HOSTS, 'types': [{'id': 5, 'name': 'Router'}], 'ioc': {}, 'index': 0,
            'default_type': 'Server'}})
        self.assertEqual('failed', outcome['kind'])
        self.assertIn('No asset type named Linux - Computer or Server', outcome['error'])


class _SiemResolver(FakeResolver):

    def get(self, name):
        if name != 'SIEM_API_KEY':
            raise KeyError(name)
        self.used.add(name)
        return _SIEM_KEY

    def mask(self, obj):
        if isinstance(obj, str):
            return obj.replace(_SIEM_KEY, '***')
        if isinstance(obj, dict):
            return {k: self.mask(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.mask(v) for v in obj]
        return obj


class TestsIocHuntRun(EngineTestCase):

    maxDiff = None

    def setUp(self):
        super().setUp()
        self.resolver = _SiemResolver()
        self.requests = []
        self.notified = []
        self.response = {'success': True, 'status_code': 200, 'response_headers': {}, 'response_body': json.dumps(
            _answer(_bucket('ws-001', 4, 'windows'), _bucket('srv-db', 10, 'linux', '10.0.0.9'),
                    _bucket('mac-7', 2, 'macos')))}

        def _send(request, **_kwargs):
            self.requests.append(request)
            return self.response

        patches = {
            'app.iris_engine.ai_workflows.nodes.ai_workflows_sandbox_execute':
                lambda source, variables, max_steps=None, timeout_seconds=None:
                    ai_workflows_sandbox_run(source, variables, max_steps=max_steps, timeout_seconds=timeout_seconds),
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

    def _run(self, ioc, document=_DOCUMENT):
        body = ai_workflows_portable_read_workflow(document)
        wf = workflow(body['graph'], trigger_type='event', trigger_config=body['trigger_config'])
        self.tool_results.update({
            'iris_case_iocs_get': ioc,
            'iris_case_assets_list': {'total': 1, 'data': [{'asset_id': 1, 'asset_name': 'WS-001'}]},
            'iris_taxonomies_list': {'asset_types': _TYPES},
        })
        run = ai_workflows_engine_start_run(wf, 'event', run_as_user_id=OWNER_ID, entity_type='case', entity_id=42,
                                            sub_entity={'type': 'ioc', 'id': 7},
                                            payload={'event': 'on_postload_ioc_create', 'data': ioc})
        for _ in range(60):
            if run.id in self.enqueued:
                self.enqueued.remove(run.id)
                ai_workflows_engine_step(run.id)
            elif run.status == 'waiting':
                [wait] = self.store.pending_waits(run.id)
                ai_workflows_engine_expire_wait(wait)
            else:
                break
        return run

    def test_hosts_outside_the_case_should_be_suggested_and_notified(self):
        run = self._run(_ioc('evil.example.org', 'domain'))
        self.assertEqual('succeeded', run.status, run.error)
        [request] = self.requests
        self.assertEqual(('POST', 'https://siem.example.org:9200/logs-*/_search'),
                         (request['method'], request['url']))
        self.assertEqual(f'ApiKey {_SIEM_KEY}', request['headers']['Authorization'])
        sent = json.loads(request['body'])
        self.assertEqual({'term': {'dns.question.name': 'evil.example.org'}},
                         sent['query']['bool']['filter'][1]['bool']['should'][0])
        [(users, args, kwargs)] = self.notified
        self.assertEqual([OWNER_ID], users)
        self.assertIn('seen on 2 host(s) outside the case', args[1])
        self.assertIn('| srv-db | 10 | linux | - |', kwargs['body'])
        first, second = self.store.suggestions
        self.assertEqual('Add srv-db to the case: it saw IOC evil.example.org', first.title)
        self.assertEqual({'tool': 'iris_case_assets_create', 'arguments': {
            'payload': {'asset_name': 'srv-db', 'asset_type_id': 3, 'asset_ip': '10.0.0.9', 'asset_tags': 'ioc-hunt',
                        'asset_description': 'Seen with IOC evil.example.org in 10 SIEM event(s) over the last 30 '
                                             'days (last seen unknown).'},
            'ioc_links': [7], 'case_identifier': 42}}, first.proposed_action)
        self.assertEqual(('mac-7', 9), (second.proposed_action['arguments']['payload']['asset_name'],
                                        second.proposed_action['arguments']['payload']['asset_type_id']))
        self.assertEqual([], [tool for tool, _args, _mode in self.executed if tool == 'iris_case_assets_create'])
        self.assertNotIn(_SIEM_KEY, json.dumps(run.context, default=str))

    def test_hits_only_on_hosts_of_the_case_should_end_quietly(self):
        self.response['response_body'] = json.dumps(_answer(_bucket('WS-001.corp.local', 4, 'windows')))
        run = self._run(_ioc('evil.example.org', 'domain'))
        self.assertEqual('succeeded', run.status, run.error)
        self.assertEqual('contained', self.steps(run)[-1].node_id)
        self.assertEqual(([], []), (self.store.suggestions, self.notified))

    def test_unsupported_ioc_should_end_without_any_request(self):
        run = self._run(_ioc('HKLM\\Run', 'regkey'))
        self.assertEqual('succeeded', run.status, run.error)
        self.assertEqual(('nothing_to_do', []), (self.steps(run)[-1].node_id, self.requests))

    def test_siem_failure_should_fail_the_run(self):
        self.response = {'success': False, 'status_code': 401, 'error': 'HTTP 401', 'response_body': '{}',
                         'response_headers': {}}
        run = self._run(_ioc('evil.example.org', 'domain'))
        self.assertEqual('failed', run.status)
        self.assertIn('401', run.error)
        self.assertEqual([], self.store.suggestions)

    def test_splunk_should_be_searched_when_chosen(self):
        self.response['response_body'] = json.dumps(_splunk(('WS-001', 4, 'Windows', None),
                                                            ('srv-db', 10, 'Linux', '2026-10-08T10:00:00+0000')))
        run = self._run(_ioc('evil.example.org', 'domain'), document=_splunk_document())
        self.assertEqual('succeeded', run.status, run.error)
        [request] = self.requests
        self.assertEqual(('POST', 'https://splunk.example.org:8089/services/search/jobs'),
                         (request['method'], request['url']))
        self.assertEqual(f'Bearer {_SIEM_KEY}', request['headers']['Authorization'])
        self.assertEqual('application/x-www-form-urlencoded', request['headers']['Content-Type'])
        form = parse_qs(request['body'].decode())
        self.assertEqual((['oneshot'], ['json'], ['-30d']),
                         (form['exec_mode'], form['output_mode'], form['earliest_time']))
        self.assertTrue(form['search'][0].startswith('search index=* (query="evil.example.org" OR '))
        [suggestion] = self.store.suggestions
        self.assertEqual({'asset_name': 'srv-db', 'asset_type_id': 3, 'asset_tags': 'ioc-hunt',
                          'asset_description': 'Seen with IOC evil.example.org in 10 SIEM event(s) over the last 30 '
                                               'days (last seen 2026-10-08T10:00:00+0000).'},
                         suggestion.proposed_action['arguments']['payload'])
        self.assertNotIn(_SIEM_KEY, json.dumps(run.context, default=str))

    def test_splunk_failure_should_fail_the_run(self):
        self.response = {'success': False, 'status_code': 401, 'error': 'HTTP 401', 'response_body': '{}',
                         'response_headers': {}}
        run = self._run(_ioc('evil.example.org', 'domain'), document=_splunk_document())
        self.assertEqual('failed', run.status)
        self.assertIn('The SIEM answered 401: HTTP 401', run.error)
