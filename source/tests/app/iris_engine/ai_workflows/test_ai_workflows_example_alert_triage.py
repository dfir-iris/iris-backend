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

"""The alert triage library workflow: it validates, its scripts read the
related-alerts graph and check the verdict, and a run turns each verdict
into the matching suggestion on the alert."""

import json
import os
from unittest import TestCase
from unittest.mock import patch

from app.iris_engine.ai_workflows.engine import ai_workflows_engine_resume_wait
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
from tests.app.iris_engine.ai_workflows.harness import text_turn
from tests.app.iris_engine.ai_workflows.harness import tool_turn
from tests.app.iris_engine.ai_workflows.harness import workflow

_PATH = os.path.join(os.path.dirname(__file__), '..', '..', '..', '..', 'app', 'iris_engine', 'ai_workflows',
                     'examples', 'alert_triage.workflow.json')
with open(_PATH, encoding='utf-8') as _handle:
    _DOCUMENT = json.load(_handle)
_NODES = {n['id']: n for n in _DOCUMENT['workflow']['graph']['nodes']}
_HOOKS = [('on_postload_alert_create', '')]
_ALERT_ID = 42


def _script(node_id, inputs):
    outcome = ai_workflows_sandbox_run(_NODES[node_id]['config']['code'], {'inputs': inputs},
                                       max_steps=_NODES[node_id]['config']['max_steps'])
    if not outcome['ok']:
        raise AssertionError(outcome)
    return outcome['result']


def _graph():
    """What iris_alerts_related_get returns for alert 42 (IOC evil.example.org,
    asset WS-001): two other alerts and an open case sharing them."""
    return {
        'nodes': [
            {'id': 'alert_42', 'label': 'Beacon to evil.example.org', 'group': 'alert'},
            {'id': 'alert_17', 'label': '[Closed][False Positive]\n Beacon to evil.example.org', 'group': 'alert'},
            {'id': 'alert_30', 'label': 'Login on WS-001', 'group': 'alert'},
            {'id': 'ioc_evil.example.org', 'label': 'evil.example.org', 'group': 'ioc'},
            {'id': 'ioc_other.example.org', 'label': 'other.example.org', 'group': 'ioc'},
            {'id': 'asset_WS-001', 'label': 'WS-001', 'group': 'asset'},
            {'id': 'case_7', 'label': 'Case #7', 'title': 'Phishing   campaign', 'group': 'case'},
        ],
        'edges': [
            {'from': 'alert_42', 'to': 'ioc_evil.example.org'},
            {'from': 'alert_42', 'to': 'asset_WS-001'},
            {'from': 'alert_17', 'to': 'ioc_evil.example.org'},
            {'from': 'alert_17', 'to': 'ioc_other.example.org'},
            {'from': 'alert_30', 'to': 'asset_WS-001'},
            {'from': 'ioc_evil.example.org', 'to': 'case_7'},
            {'from': 'asset_WS-001', 'to': 'case_7'},
        ],
    }


def _alert():
    return {'alert_id': _ALERT_ID, 'alert_title': 'Beacon to evil.example.org',
            'iocs': [{'ioc_id': 1, 'ioc_uuid': 'ioc-uuid-1', 'ioc_value': 'evil.example.org'}],
            'assets': [{'asset_id': 2, 'asset_uuid': 'asset-uuid-2', 'asset_name': 'WS-001'}]}


def _context(graph=None, alert=None):
    return _script('context', {'graph': _graph() if graph is None else graph,
                               'alert': _alert() if alert is None else alert, 'alert_id': _ALERT_ID, 'days': 30,
                               'max_items': 15})


def _decide(verdict, asked=None, open_case_ids=(7,)):
    return _script('decide', {'verdict': verdict, 'asked': asked, 'open_case_ids': list(open_case_ids),
                              'title': 'Beacon to evil.example.org', 'alert_id': _ALERT_ID})


class TestsAlertTriageDocument(TestCase):

    @patch('app.iris_engine.ai_workflows.graph.ai_workflows_db_postload_hooks', return_value=_HOOKS)
    def test_workflow_should_validate(self, _hooks):
        body = ai_workflows_portable_read_workflow(_DOCUMENT)
        self.assertEqual([], ai_workflows_graph_validate_trigger_config(body['trigger_type'], body['trigger_config']))
        self.assertEqual([], ai_workflows_graph_validate(body['graph'], body['trigger_type'], body['trigger_config'],
                                                         body['write_tool_allowlist']))

    def test_workflow_should_only_suggest(self):
        body = ai_workflows_portable_read_workflow(_DOCUMENT)
        self.assertEqual(([], ['on_postload_alert_create'], 'data.alert_severity_id >= 4'),
                         (body['write_tool_allowlist'], body['trigger_config']['hooks'],
                          body['trigger_config']['condition']))

    def test_requirements_should_match_the_graph(self):
        nodes = list(_NODES.values())
        self.assertEqual([], ai_workflows_portable_literal_secrets(nodes))
        self.assertEqual([], ai_workflows_portable_key_references(nodes))
        self.assertEqual({'keystore': [], 'tools': ai_workflows_portable_tools(nodes, [])},
                         _DOCUMENT['requirements'])


class TestsAlertTriageContextScript(TestCase):

    def test_related_alerts_and_cases_should_be_listed_with_what_they_share(self):
        result = _context()
        self.assertEqual([{'id': 7, 'closed': False, 'description': 'Phishing campaign',
                           'shared': ['evil.example.org', 'WS-001']}], result['cases'])
        self.assertEqual([{'id': 30, 'title': 'Login on WS-001', 'closed': False, 'resolution': None,
                           'shared': ['WS-001']},
                          {'id': 17, 'title': 'Beacon to evil.example.org', 'closed': True,
                           'resolution': 'False Positive', 'shared': ['evil.example.org']}], result['alerts'])
        self.assertEqual(([7], ['ioc-uuid-1'], ['asset-uuid-2'], 'Beacon to evil.example.org'),
                         (result['open_case_ids'], result['ioc_uuids'], result['asset_uuids'], result['title']))
        self.assertIn('- Case #7: Phishing campaign; shares evil.example.org, WS-001', result['text'])
        self.assertIn('- Alert #17 (closed, False Positive): Beacon to evil.example.org; shares evil.example.org',
                      result['text'])

    def test_alert_missing_from_the_graph_should_share_every_indicator(self):
        graph = _graph()
        graph['nodes'] = [n for n in graph['nodes'] if n['id'] != 'alert_42']
        result = _context(graph=graph)
        self.assertEqual(['evil.example.org', 'other.example.org'],
                         [a for a in result['alerts'] if a['id'] == 17][0]['shared'])

    def test_failed_lookup_should_say_nothing_is_related(self):
        result = _context(graph={}, alert={})
        self.assertEqual(([], [], 'Alert #42'), (result['alerts'], result['cases'], result['title']))
        self.assertEqual('No alert or case shares an IOC or an asset with this alert.', result['text'])

    def test_closed_cases_should_not_be_merge_targets(self):
        graph = _graph()
        graph['nodes'][-1]['label'] = '[Closed] Case #7'
        self.assertEqual([], _context(graph=graph)['open_case_ids'])


class TestsAlertTriageDecideScript(TestCase):

    def test_benign_should_close_with_the_resolution(self):
        self.assertEqual(('close', 'False Positive'), (_decide({'verdict': 'benign'})['route'],
                                                       _decide({'verdict': 'benign'})['resolution']))
        self.assertEqual('Legitimate', _decide({'verdict': 'benign', 'benign_kind': 'legitimate'})['resolution'])

    def test_merge_should_target_a_related_open_case_only(self):
        result = _decide({'verdict': 'merge', 'target_case_id': '7'})
        self.assertEqual(('merge', 7), (result['route'], result['target_case_id']))
        result = _decide({'verdict': 'merge', 'target_case_id': 99})
        self.assertEqual(('review', None), (result['route'], result['target_case_id']))
        self.assertIn('case #99', result['note'])

    def test_escalate_should_have_a_case_title(self):
        self.assertEqual('Beaconing from WS-001',
                         _decide({'verdict': 'escalate', 'case_title': 'Beaconing from WS-001'})['case_title'])
        self.assertEqual('Alert #42: Beacon to evil.example.org', _decide({'verdict': 'escalate'})['case_title'])

    def test_questions_should_be_asked_once(self):
        verdict = {'verdict': 'needs_info', 'missing': ['Is WS-001 a server?', '', 'x' * 100]}
        result = _decide(verdict)
        self.assertEqual(('ask', ['Is WS-001 a server?', 'x' * 60]), (result['route'], result['missing']))
        result = _decide(verdict, asked=True)
        self.assertEqual('review', result['route'])
        self.assertIn('already answered', result['note'])
        self.assertEqual('review', _decide({'verdict': 'needs_info'})['route'])

    def test_other_verdicts_should_go_to_an_analyst(self):
        for verdict in ({'verdict': 'suspicious'}, {'verdict': 'whatever'}, None):
            self.assertEqual('review', _decide(verdict)['route'])


class TestsAlertTriageIdsScript(TestCase):

    _TAXONOMIES = {
        'alert_statuses': [{'id': 1, 'name': 'New'}, {'id': 6, 'name': 'Closed'}],
        'alert_resolutions': [{'id': 1, 'name': 'False Positive'}, {'id': 6, 'name': 'Legitimate'}],
    }

    def test_ids_should_be_found_by_name(self):
        self.assertEqual({'status_id': 6, 'resolution_id': 6},
                         _script('ids', {'taxonomies': self._TAXONOMIES, 'resolution': 'legitimate'}))

    def test_missing_status_should_fail(self):
        outcome = ai_workflows_sandbox_run(_NODES['ids']['config']['code'], {'inputs': {
            'taxonomies': {'alert_statuses': [], 'alert_resolutions': []}, 'resolution': 'False Positive'}})
        self.assertEqual(('failed', 'No alert status is named Closed'), (outcome['kind'], outcome['error']))


class TestsAlertTriageRun(EngineTestCase):

    maxDiff = None

    def setUp(self):
        super().setUp()
        patches = {
            'app.iris_engine.ai_workflows.nodes.ai_workflows_sandbox_execute':
                lambda source, variables, max_steps=None, timeout_seconds=None:
                    ai_workflows_sandbox_run(source, variables, max_steps=max_steps, timeout_seconds=timeout_seconds),
            # The cases a merge may target belong to the alert's customer
            'app.iris_engine.ai_workflows.agent.ai_workflows_runtime_db_case_customers':
                lambda ids: {int(i): 1 for i in ids},
        }
        for target, replacement in patches.items():
            patcher = patch(target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.tool_results.update({
            'iris_alerts_get': _alert(),
            'iris_alerts_related_get': _graph(),
            'iris_taxonomies_list': TestsAlertTriageIdsScript._TAXONOMIES,
        })

    def _start(self):
        body = ai_workflows_portable_read_workflow(_DOCUMENT)
        wf = workflow(body['graph'], trigger_type='event', trigger_config=body['trigger_config'])
        run = ai_workflows_engine_start_run(wf, 'event', run_as_user_id=OWNER_ID, entity_type='alert',
                                            entity_id=_ALERT_ID,
                                            payload={'event': 'on_postload_alert_create', 'data': _alert()})
        return self._settle(run)

    def _settle(self, run):
        for _ in range(60):
            if run.id not in self.enqueued:
                break
            self.enqueued.remove(run.id)
            ai_workflows_engine_step(run.id)
        return run

    def _verdict(self, **output):
        return tool_turn('set_output', {'confidence': 0.8, 'rationale': 'Seen before.', **output})

    def test_benign_alert_should_get_a_close_suggestion(self):
        provider = self.use_provider(self._verdict(verdict='benign'), text_turn('done'))
        run = self._start()
        self.assertEqual('succeeded', run.status, run.error)
        self.assertIn('Case #7: Phishing campaign', json.dumps(provider.calls[0]['messages'], default=str))
        [suggestion] = self.store.suggestions
        self.assertEqual('Close as False Positive: Beacon to evil.example.org', suggestion.title)
        self.assertEqual({'tool': 'iris_alerts_update', 'arguments': {
            'payload': {'alert_status_id': 6, 'alert_resolution_status_id': 1}, 'alert_identifier': _ALERT_ID}},
            suggestion.proposed_action)
        self.assertIn('**AI verdict: benign** (confidence 0.8)', suggestion.body)
        self.assertIn('Seen before.', suggestion.body)

    def test_known_incident_should_get_a_merge_suggestion(self):
        self.use_provider(self._verdict(verdict='merge', target_case_id=7), text_turn('done'))
        run = self._start()
        self.assertEqual('succeeded', run.status, run.error)
        [suggestion] = self.store.suggestions
        self.assertEqual(('merge_into_case', 'Merge into case #7: Beacon to evil.example.org'),
                         (suggestion.kind, suggestion.title))
        self.assertEqual({'tool': 'iris_alerts_merge', 'arguments': {
            'target_case_id': 7, 'note': 'AI triage: Seen before.', 'import_as_event': True,
            'iocs_import_list': ['ioc-uuid-1'], 'assets_import_list': ['asset-uuid-2'],
            'alert_identifier': _ALERT_ID}}, suggestion.proposed_action)

    def test_new_incident_should_get_a_case_suggestion(self):
        self.use_provider(self._verdict(verdict='escalate', case_title='Beaconing from WS-001'), text_turn('done'))
        run = self._start()
        self.assertEqual('succeeded', run.status, run.error)
        [suggestion] = self.store.suggestions
        self.assertEqual(('create_case', 'Open a case: Beaconing from WS-001'), (suggestion.kind, suggestion.title))
        self.assertEqual({'tool': 'iris_alerts_escalate', 'arguments': {
            'case_title': 'Beaconing from WS-001', 'note': 'AI triage: Seen before.', 'import_as_event': True,
            'case_tags': 'ai-triage', 'iocs_import_list': ['ioc-uuid-1'], 'assets_import_list': ['asset-uuid-2'],
            'alert_identifier': _ALERT_ID}}, suggestion.proposed_action)

    def test_unrelated_merge_target_should_go_to_an_analyst(self):
        self.use_provider(self._verdict(verdict='merge', target_case_id=99), text_turn('done'))
        run = self._start()
        self.assertEqual('succeeded', run.status, run.error)
        [suggestion] = self.store.suggestions
        self.assertEqual(('Review: Beacon to evil.example.org', None), (suggestion.title, suggestion.proposed_action))
        self.assertIn('not one of the open cases', suggestion.body)

    def test_missing_facts_should_be_asked_then_triaged_again(self):
        provider = self.use_provider(self._verdict(verdict='needs_info', missing=['Is WS-001 a server?']),
                                     text_turn('done'), self._verdict(verdict='benign', benign_kind='legitimate'),
                                     text_turn('done'))
        run = self._start()
        self.assertEqual('waiting', run.status, run.error)
        [wait] = self.store.pending_waits(run.id)
        self.assertEqual('ask', wait.node_id)
        ai_workflows_engine_resume_wait(wait, {'answer': {'Is WS-001 a server?': 'Yes, the backup server'},
                                               'answered_by': 'analyst'}, resolved_by_id=OWNER_ID)
        run = self._settle(run)
        self.assertEqual('succeeded', run.status, run.error)
        self.assertIn('Yes, the backup server', json.dumps(provider.calls[-1]['messages'], default=str))
        [question, suggestion] = self.store.suggestions
        self.assertEqual('info_request', question.kind)
        self.assertEqual('Close as Legitimate: Beacon to evil.example.org', suggestion.title)
        self.assertEqual(6, suggestion.proposed_action['arguments']['payload']['alert_resolution_status_id'])

    def test_agent_failure_should_fail_the_run(self):
        self.use_provider(text_turn('no idea'), text_turn('still no idea'))
        run = self._start()
        self.assertEqual('failed', run.status)
        self.assertEqual('triage_failed', self.steps(run)[-1].node_id)
        self.assertEqual([], self.store.suggestions)
