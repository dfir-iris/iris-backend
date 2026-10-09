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

"""The case closure documentation review library workflow: it runs only
when a case was just closed, its script turns the AI review into
proposals, and a run suggests them on the case."""

import json
import os
from unittest import TestCase
from unittest.mock import patch

from app.iris_engine.ai_workflows.context import ai_workflows_context_eval_expression
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
                     'examples', 'case_closure_documentation_review.workflow.json')
with open(_PATH, encoding='utf-8') as _handle:
    _DOCUMENT = json.load(_handle)
_NODES = {n['id']: n for n in _DOCUMENT['workflow']['graph']['nodes']}
_HOOKS = [('on_postload_case_update', '')]
_FOOTER = '_Drafted by an AI workflow from the case content when the case was closed; review it before accepting._'
_REVIEW = {
    'assessment': {'quality': 'poor', 'summary': 'The notes stop at the containment.'},
    'gaps': ['No root cause', '', 'Task #4 closed without a result'],
    'closure_report': {
        'summary': 'A phishing email led to a token theft on WS-001; the token was revoked.',
        'timeline': [{'when': '2026-10-01 08:12', 'what': 'Phishing email | opened'}, {'when': '', 'what': 'Reset'},
                     {'when': '2026-10-02'}],
        'scope': ['WS-001', 'jdoe'],
        'root_cause': 'MFA fatigue',
        'actions_taken': ['Revoked the sessions of jdoe'],
        'lessons_learned': ['Enable number matching'],
        'open_items': ['Check the mailbox rules'],
    },
    'case_description': 'Token theft on WS-001 after a phishing email, contained on 2026-10-02.',
    'note_improvements': [
        {'note_id': 12, 'note_title': 'Containment', 'note_content': 'Sessions of jdoe revoked on 2026-10-02.',
         'reason': 'Says what was done and when'},
        {'note_id': 12, 'note_content': 'A duplicate of the same note', 'reason': 'x'},
        {'note_id': 'x', 'note_content': 'Not a note id at all, skipped', 'reason': 'x'},
        {'note_id': 13, 'note_content': 'short', 'reason': 'x'},
        {'note_id': 14, 'note_title': '', 'note_content': 'Collected the triage image of WS-001.', 'reason': ''},
    ],
}
# What the output schema lets through: the script still has to drop the duplicate and the short note
_VALID_REVIEW = {**_REVIEW,
                 'closure_report': {**_REVIEW['closure_report'],
                                    'timeline': _REVIEW['closure_report']['timeline'][:2]},
                 'note_improvements': [n for n in _REVIEW['note_improvements'] if isinstance(n['note_id'], int)]}


def _plan(review=None, description='Phishing', max_notes=3):
    return ai_workflows_sandbox_run(_NODES['plan']['config']['code'], {'inputs': {
        'review': _REVIEW if review is None else review, 'description': description, 'max_notes': max_notes}},
        max_steps=_NODES['plan']['config']['max_steps'])['result']


def _closed(state='Closed', close_date='2026-10-09', timestamp='2026-10-09T14:03:11.120000+00:00'):
    data = {'case_id': 42, 'state': {'state_name': state} if state else None, 'close_date': close_date}
    event = {'event': 'on_postload_case_update', 'timestamp': timestamp, 'data': data}
    return ai_workflows_context_eval_expression(_DOCUMENT['workflow']['trigger_config']['condition'], {
        'event': event, 'payload': event, 'data': data, 'hook': event['event'], 'entity_type': 'case',
        'entity_id': 42})


class TestsCaseClosureDocument(TestCase):

    @patch('app.iris_engine.ai_workflows.graph.ai_workflows_db_postload_hooks', return_value=_HOOKS)
    def test_workflow_should_validate(self, _hooks):
        body = ai_workflows_portable_read_workflow(_DOCUMENT)
        self.assertEqual([], ai_workflows_graph_validate_trigger_config(body['trigger_type'], body['trigger_config']))
        self.assertEqual([], ai_workflows_graph_validate(body['graph'], body['trigger_type'], body['trigger_config'],
                                                         body['write_tool_allowlist']))

    def test_workflow_should_only_suggest(self):
        body = ai_workflows_portable_read_workflow(_DOCUMENT)
        self.assertEqual(([], ['on_postload_case_update'], 1440),
                         (body['write_tool_allowlist'], body['trigger_config']['hooks'],
                          body['trigger_config']['dedup_minutes']))

    def test_requirements_should_match_the_graph(self):
        nodes = list(_NODES.values())
        self.assertEqual([], ai_workflows_portable_literal_secrets(nodes))
        self.assertEqual([], ai_workflows_portable_key_references(nodes))
        self.assertEqual({'keystore': [], 'tools': ai_workflows_portable_tools(nodes, [])},
                         _DOCUMENT['requirements'])

    def test_case_closed_today_should_run(self):
        self.assertTrue(_closed())
        self.assertTrue(_closed(close_date='2026-10-09T14:03:10'))

    def test_open_case_should_not_run(self):
        self.assertFalse(_closed(state='Open'))
        self.assertFalse(_closed(state=None))

    def test_update_of_a_case_closed_before_should_not_run(self):
        self.assertFalse(_closed(close_date='2026-10-01'))
        self.assertFalse(_closed(close_date=None))


class TestsCaseClosurePlanScript(TestCase):

    def test_report_should_hold_every_section(self):
        report = _plan()['report']
        self.assertEqual('\n'.join([
            '## Summary', '', _REVIEW['closure_report']['summary'], '',
            '## Timeline', '', '| When | What |', '| --- | --- |',
            '| 2026-10-01 08:12 | Phishing email \\| opened |', '| Unknown | Reset |', '',
            '## Scope', '', '- WS-001', '- jdoe', '',
            '## Root cause', '', 'MFA fatigue', '',
            '## Actions taken', '', '- Revoked the sessions of jdoe', '',
            '## Lessons learned', '', '- Enable number matching', '',
            '## Open items', '', '- [ ] Check the mailbox rules', '',
            _FOOTER]), report)

    def test_assessment_should_give_the_quality_and_the_gaps(self):
        result = _plan()
        self.assertEqual(('poor', ['No root cause', 'Task #4 closed without a result']),
                         (result['quality'], result['gaps']))
        self.assertEqual('**Documentation quality:** poor\n\nThe notes stop at the containment.\n\n**Gaps**\n\n'
                         '- No root cause\n- Task #4 closed without a result', result['assessment'])

    def test_note_rewrites_should_be_valid_and_one_per_note(self):
        result = _plan()
        self.assertEqual([
            {'note_identifier': 12, 'payload': {'note_title': 'Containment',
                                                'note_content': 'Sessions of jdoe revoked on 2026-10-02.'},
             'title': 'Containment', 'reason': 'Says what was done and when'},
            {'note_identifier': 14, 'payload': {'note_content': 'Collected the triage image of WS-001.'},
             'title': 'Note #14', 'reason': 'Clearer and more complete.'}], result['notes'])
        self.assertEqual((2, True, True, False), (result['note_count'], result['has_note_1'], result['has_note_2'],
                                                  result['has_note_3']))
        self.assertEqual(1, _plan(max_notes=1)['note_count'])

    def test_unchanged_description_should_not_be_proposed(self):
        result = _plan(description=_REVIEW['case_description'])
        self.assertEqual(('', False), (result['description'], result['has_description']))
        self.assertTrue(_plan()['has_description'])

    def test_documented_case_should_propose_nothing(self):
        result = _plan(review={'assessment': {'quality': 'good', 'summary': 'Complete.'}, 'gaps': [],
                               'closure_report': {'summary': ''}, 'case_description': ''})
        self.assertEqual((False, False, 0), (result['has_report'], result['has_description'], result['note_count']))

    def test_malformed_review_should_not_fail(self):
        result = _plan(review={'assessment': 'x', 'gaps': 'x', 'closure_report': {'summary': 3, 'timeline': 'x'},
                               'note_improvements': {'note_id': 1}})
        self.assertEqual(('unknown', [], False, 0),
                         (result['quality'], result['gaps'], result['has_report'], result['note_count']))


class TestsCaseClosureRun(EngineTestCase):

    maxDiff = None

    def setUp(self):
        super().setUp()
        patcher = patch('app.iris_engine.ai_workflows.nodes.ai_workflows_sandbox_execute',
                        lambda source, variables, max_steps=None, timeout_seconds=None:
                            ai_workflows_sandbox_run(source, variables, max_steps=max_steps,
                                                     timeout_seconds=timeout_seconds))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _run(self):
        body = ai_workflows_portable_read_workflow(_DOCUMENT)
        wf = workflow(body['graph'], trigger_type='event', trigger_config=body['trigger_config'])
        run = ai_workflows_engine_start_run(wf, 'event', run_as_user_id=OWNER_ID, entity_type='case', entity_id=42,
                                            payload={'event': 'on_postload_case_update', 'data': {
                                                'case_id': 42, 'case_description': 'Phishing',
                                                'state': {'state_name': 'Closed'}}})
        for _ in range(40):
            if run.id not in self.enqueued:
                break
            self.enqueued.remove(run.id)
            ai_workflows_engine_step(run.id)
        return run

    def test_review_should_suggest_the_report_the_description_and_the_rewrites(self):
        provider = self.use_provider(tool_turn('set_output', _VALID_REVIEW), text_turn('done'))
        run = self._run()
        self.assertEqual('succeeded', run.status, run.error)
        self.assertIn('iris_case_notes_get', json.dumps(provider.calls[0]['tools'], default=str))
        report, description, *notes = self.store.suggestions
        self.assertEqual('Closure report for case #42', report.title)
        self.assertTrue(report.body.startswith('**Documentation quality:** poor\n\n'))
        self.assertEqual({'tool': 'iris_case_notes_create', 'arguments': {
            'payload': {'note_title': 'Closure report (AI draft)', 'note_content': _plan()['report']},
            'case_identifier': 42}}, report.proposed_action)
        self.assertEqual({'tool': 'iris_cases_update', 'arguments': {
            'payload': {'case_description': _REVIEW['case_description']}, 'case_identifier': 42}},
            description.proposed_action)
        self.assertEqual(['Rewrite of the note "Containment"', 'Rewrite of the note "Note #14"'],
                         [n.title for n in notes])
        self.assertEqual({'tool': 'iris_case_notes_update', 'arguments': {
            'note_identifier': 14, 'payload': {'note_content': 'Collected the triage image of WS-001.'},
            'case_identifier': 42}}, notes[1].proposed_action)
        self.assertTrue(notes[0].body.startswith('Says what was done and when\n\n'))

    def test_partial_review_should_suggest_only_what_it_holds(self):
        review = {'assessment': {'quality': 'fair', 'summary': 'Missing the description.'}, 'gaps': [],
                  'closure_report': {'summary': ''}, 'case_description': 'Token theft on WS-001, contained.',
                  'note_improvements': [{'note_id': 7, 'note_content': 'The full rewritten note body.',
                                         'reason': 'Clearer'}]}
        self.use_provider(tool_turn('set_output', review), text_turn('done'))
        run = self._run()
        self.assertEqual('succeeded', run.status, run.error)
        self.assertEqual(['Better description for case #42', 'Rewrite of the note "Note #7"'],
                         [s.title for s in self.store.suggestions])

    def test_documented_case_should_suggest_nothing(self):
        review = {'assessment': {'quality': 'good', 'summary': 'Complete.'}, 'gaps': [],
                  'closure_report': {'summary': ''}}
        self.use_provider(tool_turn('set_output', review), text_turn('done'))
        run = self._run()
        self.assertEqual('succeeded', run.status, run.error)
        self.assertEqual([], self.store.suggestions)

    def test_agent_failure_should_fail_the_run(self):
        self.use_provider(text_turn('no idea'), text_turn('still no idea'))
        run = self._run()
        self.assertEqual('failed', run.status)
        self.assertEqual('review_failed', self.steps(run)[-1].node_id)
        self.assertEqual([], self.store.suggestions)
