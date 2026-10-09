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

"""The case kickoff library workflow: it validates, its script turns the
AI plan into a note and tasks, and a run suggests them on the new case."""

import json
import os
from unittest import TestCase
from unittest.mock import patch

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
                     'examples', 'case_kickoff.workflow.json')
with open(_PATH, encoding='utf-8') as _handle:
    _DOCUMENT = json.load(_handle)
_NODES = {n['id']: n for n in _DOCUMENT['workflow']['graph']['nodes']}
_HOOKS = [('on_postload_case_create', '')]
_TAXONOMIES = {'task_statuses': [{'id': 2, 'name': 'In progress'}, {'id': 1, 'name': 'To do'}]}
_PLAN = {
    'summary': 'Phishing led to a token theft on WS-001.',
    'scope': ['WS-001', 'jdoe', ''],
    'hypotheses': [{'hypothesis': 'The token was replayed', 'how_to_check': 'Sign-in logs'}, {'how_to_check': 'x'}],
    'open_questions': ['Was MFA prompted?'],
    'first_tasks': [{'title': 'Triage image of WS-001', 'description': 'KAPE'},
                    {'title': 'Reset jdoe', 'description': 'Revoke sessions too'},
                    {'title': 'x'},
                    {'title': 'Search the SIEM', 'description': 'evil.example.org'},
                    {'title': 'Block the domain', 'description': 'On the proxy'}],
}


def _plan(plan=None, taxonomies=None, max_tasks=3):
    return ai_workflows_sandbox_run(_NODES['plan']['config']['code'], {'inputs': {
        'plan': _PLAN if plan is None else plan, 'taxonomies': _TAXONOMIES if taxonomies is None else taxonomies,
        'max_tasks': max_tasks}}, max_steps=_NODES['plan']['config']['max_steps'])


class TestsCaseKickoffDocument(TestCase):

    @patch('app.iris_engine.ai_workflows.graph.ai_workflows_db_postload_hooks', return_value=_HOOKS)
    def test_workflow_should_validate(self, _hooks):
        body = ai_workflows_portable_read_workflow(_DOCUMENT)
        self.assertEqual([], ai_workflows_graph_validate_trigger_config(body['trigger_type'], body['trigger_config']))
        self.assertEqual([], ai_workflows_graph_validate(body['graph'], body['trigger_type'], body['trigger_config'],
                                                         body['write_tool_allowlist']))

    def test_workflow_should_only_suggest(self):
        body = ai_workflows_portable_read_workflow(_DOCUMENT)
        self.assertEqual(([], ['on_postload_case_create']),
                         (body['write_tool_allowlist'], body['trigger_config']['hooks']))

    def test_requirements_should_match_the_graph(self):
        nodes = list(_NODES.values())
        self.assertEqual([], ai_workflows_portable_literal_secrets(nodes))
        self.assertEqual([], ai_workflows_portable_key_references(nodes))
        self.assertEqual({'keystore': [], 'tools': ai_workflows_portable_tools(nodes, [])},
                         _DOCUMENT['requirements'])


class TestsCaseKickoffPlanScript(TestCase):

    def test_note_should_hold_the_whole_plan(self):
        note = _plan()['result']['note']
        self.assertTrue(note.startswith('Phishing led to a token theft on WS-001.\n\n## Scope\n\n- WS-001\n- jdoe\n'))
        self.assertIn('## Hypotheses\n\n- The token was replayed Check: Sign-in logs\n\n', note)
        self.assertIn('## Open questions\n\n- [ ] Was MFA prompted?\n', note)
        self.assertIn('## First tasks\n\n- [ ] Triage image of WS-001\n- [ ] Reset jdoe\n- [ ] Search the SIEM\n'
                      '- [ ] Block the domain\n', note)
        self.assertTrue(note.endswith('_Drafted by an AI workflow when the case was opened; review and complete it._'))

    def test_tasks_should_be_capped_and_created_to_do(self):
        result = _plan()['result']
        self.assertEqual([{'task_title': 'Triage image of WS-001', 'task_description': 'KAPE', 'task_status_id': 1},
                          {'task_title': 'Reset jdoe', 'task_description': 'Revoke sessions too', 'task_status_id': 1},
                          {'task_title': 'Search the SIEM', 'task_description': 'evil.example.org',
                           'task_status_id': 1}], result['tasks'])
        self.assertEqual((3, True, True, True), (result['task_count'], result['has_1'], result['has_2'],
                                                 result['has_3']))

    def test_fewer_tasks_should_skip_the_others(self):
        result = _plan(plan={'summary': 'S', 'first_tasks': [{'title': 'Only one'}]})['result']
        self.assertEqual((1, True, False, False), (result['task_count'], result['has_1'], result['has_2'],
                                                   result['has_3']))
        self.assertEqual('S\n\n## First tasks\n\n- [ ] Only one\n\n'
                         '_Drafted by an AI workflow when the case was opened; review and complete it._',
                         result['note'])

    def test_missing_to_do_should_use_the_lowest_status(self):
        result = _plan(taxonomies={'task_statuses': [{'id': 5, 'name': 'Open'}, {'id': 3, 'name': 'Started'}]})
        self.assertEqual(3, result['result']['tasks'][0]['task_status_id'])

    def test_no_status_should_fail(self):
        outcome = _plan(taxonomies={})
        self.assertEqual(('failed', 'No task status is defined'), (outcome['kind'], outcome['error']))


class TestsCaseKickoffRun(EngineTestCase):

    maxDiff = None

    def setUp(self):
        super().setUp()
        patcher = patch('app.iris_engine.ai_workflows.nodes.ai_workflows_sandbox_execute',
                        lambda source, variables, max_steps=None, timeout_seconds=None:
                            ai_workflows_sandbox_run(source, variables, max_steps=max_steps,
                                                     timeout_seconds=timeout_seconds))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tool_results['iris_taxonomies_list'] = _TAXONOMIES

    def _run(self):
        body = ai_workflows_portable_read_workflow(_DOCUMENT)
        wf = workflow(body['graph'], trigger_type='event', trigger_config=body['trigger_config'])
        run = ai_workflows_engine_start_run(wf, 'event', run_as_user_id=OWNER_ID, entity_type='case', entity_id=42,
                                            payload={'event': 'on_postload_case_create', 'data': {'case_id': 42}})
        for _ in range(40):
            if run.id not in self.enqueued:
                break
            self.enqueued.remove(run.id)
            ai_workflows_engine_step(run.id)
        return run

    def test_kickoff_should_suggest_a_note_and_the_first_tasks(self):
        plan = {**_PLAN, 'hypotheses': _PLAN['hypotheses'][:1]}
        provider = self.use_provider(tool_turn('set_output', plan), text_turn('done'))
        run = self._run()
        self.assertEqual('succeeded', run.status, run.error)
        self.assertIn('iris_case_iocs_list', json.dumps(provider.calls[0]['tools'], default=str))
        note, *tasks = self.store.suggestions
        self.assertEqual('Kickoff note for case #42', note.title)
        self.assertEqual({'tool': 'iris_case_notes_create', 'arguments': {
            'payload': {'note_title': 'Kickoff (AI draft)', 'note_content': note.body}, 'case_identifier': 42}},
            note.proposed_action)
        self.assertEqual(['Task: Triage image of WS-001', 'Task: Reset jdoe', 'Task: Search the SIEM'],
                         [t.title for t in tasks])
        self.assertEqual({'tool': 'iris_case_tasks_create', 'arguments': {
            'payload': {'task_title': 'Reset jdoe', 'task_description': 'Revoke sessions too', 'task_status_id': 1},
            'case_identifier': 42}}, tasks[1].proposed_action)
        self.assertTrue(tasks[1].body.startswith('Revoke sessions too\n\n'))

    def test_plan_without_tasks_should_suggest_the_note_only(self):
        self.use_provider(tool_turn('set_output', {'summary': 'Too early to say.', 'first_tasks': []}), text_turn('done'))
        run = self._run()
        self.assertEqual('succeeded', run.status, run.error)
        self.assertEqual(['Kickoff note for case #42'], [s.title for s in self.store.suggestions])

    def test_agent_failure_should_fail_the_run(self):
        self.use_provider(text_turn('no idea'), text_turn('still no idea'))
        run = self._run()
        self.assertEqual('failed', run.status)
        self.assertEqual('kickoff_failed', self.steps(run)[-1].node_id)
        self.assertEqual([], self.store.suggestions)
