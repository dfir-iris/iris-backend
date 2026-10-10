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

"""Run state machine: stepping a run through its nodes with the
persistence layer, the tools and the LLM provider replaced by fakes."""

import json
from unittest.mock import patch

from app.iris_engine.ai_workflows.engine import ai_workflows_engine_cancel_run
from app.iris_engine.ai_workflows.engine import ai_workflows_engine_expire_wait
from app.iris_engine.ai_workflows.engine import ai_workflows_engine_recover_stale
from app.iris_engine.ai_workflows.engine import ai_workflows_engine_resume_wait
from app.iris_engine.ai_workflows.engine import ai_workflows_engine_step
from app.iris_engine.webhooks.render import MASK
from tests.app.iris_engine.ai_workflows.harness import OWNER_ID
from tests.app.iris_engine.ai_workflows.harness import SECRET_NAME
from tests.app.iris_engine.ai_workflows.harness import SECRET_VALUE
from tests.app.iris_engine.ai_workflows.harness import EngineTestCase
from tests.app.iris_engine.ai_workflows.harness import chain
from tests.app.iris_engine.ai_workflows.harness import text_turn
from tests.app.iris_engine.ai_workflows.harness import tool_turn
from tests.app.iris_engine.ai_workflows.harness import workflow

_WRITE_TOOL = 'iris_case_notes_create'
_READ_TOOL = 'iris_cases_get'


def _action(node_id, tool, arguments=None):
    return {'id': node_id, 'type': 'action', 'config': {'tool': tool, 'arguments': arguments or {}}}


def _agent(node_id, prompt, tools=None):
    return {'id': node_id, 'type': 'ai_agent', 'config': {'prompt': prompt, 'tools': tools or []}}


class TestsStartRun(EngineTestCase):

    def test_start_run_should_queue_the_trigger(self):
        run = self.start(workflow(chain()))
        self.assertEqual('running', run.status)
        self.assertEqual(['trigger'], run.pending_nodes)
        self.assertEqual([run.id], self.enqueued)
        self.assertEqual(1, run.customer_id)

    def test_start_run_should_be_skipped_when_the_run_as_user_cannot_access_the_entity(self):
        with self._patch_access(False):
            run = self.start(workflow(chain()))
        self.assertEqual('skipped', run.status)
        self.assertIn('cannot access case #42', run.error)
        self.assertEqual([], self.enqueued)

    def test_start_run_should_be_skipped_when_the_entity_is_outside_the_customer_scope(self):
        run = self.start(workflow(chain(), customer_scope=[7]))
        self.assertEqual('skipped', run.status)
        self.assertIn('outside the workflow scope', run.error)

    def test_start_run_should_be_skipped_when_the_owner_is_gone(self):
        wf = workflow(chain(), owner_id=999)
        from app.iris_engine.ai_workflows.engine import ai_workflows_engine_start_run
        run = ai_workflows_engine_start_run(wf, 'manual', run_as_user_id=999, entity_type='case', entity_id=1)
        self.assertEqual('skipped', run.status)
        self.assertEqual([], self.enqueued)

    def test_start_run_should_snapshot_the_definition(self):
        wf = workflow(chain(), allowlist=[_WRITE_TOOL])
        run = self.start(wf)
        wf.write_tool_allowlist = []
        self.assertEqual([_WRITE_TOOL], run.definition_snapshot['write_tool_allowlist'])

    def _patch_access(self, allowed):
        return patch('app.iris_engine.ai_workflows.engine.ai_workflows_entities_user_can_access',
                     lambda _user, _type, _id: allowed)


class TestsStepping(EngineTestCase):

    def test_run_should_succeed_through_a_linear_graph(self):
        graph = chain({'id': 'vars', 'type': 'set_variables',
                       'config': {'variables': [{'name': 'title', 'value': '{{ entity.title }}'}]}})
        run = self.run_to_rest(workflow(graph))
        self.assertEqual('succeeded', run.status)
        self.assertEqual(['trigger', 'vars'], [s.node_id for s in self.steps(run)])
        self.assertEqual({'title': 'Phishing'}, run.context['vars'])
        self.assertFalse(run.is_executing)
        self.assertEqual([run], self.published)

    def test_progress_should_be_pushed_live(self):
        graph = chain({'id': 'vars', 'type': 'set_variables', 'config': {'variables': []}})
        run = self.run_to_rest(workflow(graph))
        steps = [(e['step']['node_id'], e['step']['status']) for e in self.live if e['step']]
        self.assertEqual([('trigger', 'running'), ('trigger', 'succeeded'), ('vars', 'running'),
                          ('vars', 'succeeded')], steps)
        self.assertEqual('running', self.live[0]['status'])
        self.assertIsNone(self.live[0]['step'])
        self.assertEqual('succeeded', self.live[-1]['status'])
        self.assertEqual({str(run.uuid)}, {e['run_uuid'] for e in self.live})

    def test_cancel_should_be_pushed_live(self):
        run = self.start(workflow(chain({'id': 'vars', 'type': 'set_variables', 'config': {'variables': []}})))
        ai_workflows_engine_cancel_run(run, OWNER_ID)
        self.assertEqual('cancelled', self.live[-1]['status'])

    def test_condition_should_follow_the_matching_port(self):
        graph = {
            'nodes': [
                {'id': 'trigger', 'type': 'trigger', 'config': {}},
                {'id': 'check', 'type': 'condition', 'config': {
                    'rules': [{'path': 'entity.title', 'operator': 'contains', 'value': 'Phish'}]}},
                {'id': 'yes', 'type': 'stop', 'config': {'status': 'succeeded', 'reason': 'matched'}},
                {'id': 'no', 'type': 'stop', 'config': {'status': 'failed', 'reason': 'no match'}},
            ],
            'edges': [
                {'id': 'e1', 'source': 'trigger', 'target': 'check', 'source_port': 'out'},
                {'id': 'e2', 'source': 'check', 'target': 'yes', 'source_port': 'true'},
                {'id': 'e3', 'source': 'check', 'target': 'no', 'sourceHandle': 'false'},
            ],
        }
        run = self.run_to_rest(workflow(graph))
        self.assertEqual('succeeded', run.status)
        self.assertEqual(['trigger', 'check', 'yes'], [s.node_id for s in self.steps(run)])
        self.assertEqual('matched', run.context['stop_reason'])

    def test_failing_node_without_error_port_should_fail_the_run(self):
        run = self.run_to_rest(workflow(chain(_action('act', 'not_a_tool'))))
        self.assertEqual('failed', run.status)
        self.assertIn('not_a_tool', run.error)
        self.assertEqual('failed', self.steps(run)[-1].status)

    def test_failing_node_with_error_port_should_continue_through_it(self):
        graph = chain(_action('act', 'not_a_tool'), {'id': 'end', 'type': 'stop', 'config': {}},
                      ports={'act': 'error'})
        run = self.run_to_rest(workflow(graph))
        self.assertEqual('succeeded', run.status)
        self.assertEqual('error', self.steps(run)[1].port)

    def test_step_limit_should_fail_the_run(self):
        graph = chain({'id': 'a', 'type': 'set_variables', 'config': {}},
                      {'id': 'b', 'type': 'set_variables', 'config': {}})
        from app import app
        app.config['AI_WORKFLOWS_MAX_STEPS_PER_RUN'] = 2
        try:
            run = self.run_to_rest(workflow(graph))
        finally:
            app.config.pop('AI_WORKFLOWS_MAX_STEPS_PER_RUN')
        self.assertEqual('failed', run.status)
        self.assertIn('Step limit', run.error)

    def test_loop_without_waiting_node_should_run_until_its_condition_exits(self):
        graph = {
            'nodes': [
                {'id': 'trigger', 'type': 'trigger', 'config': {}},
                {'id': 'count', 'type': 'set_variables', 'config': {
                    'variables': [{'name': 'count', 'value': '{{ (vars.count | default(0)) + 1 }}'}]}},
                {'id': 'again', 'type': 'condition', 'config': {'mode': 'expression',
                                                                'expression': 'vars.count < 30'}},
                {'id': 'end', 'type': 'stop', 'config': {'status': 'succeeded', 'reason': 'done'}},
            ],
            'edges': [
                {'id': 'e1', 'source': 'trigger', 'target': 'count', 'source_port': 'out'},
                {'id': 'e2', 'source': 'count', 'target': 'again', 'source_port': 'out'},
                {'id': 'e3', 'source': 'again', 'target': 'count', 'source_port': 'true'},
                {'id': 'e4', 'source': 'again', 'target': 'end', 'source_port': 'false'},
            ],
        }
        run = self.run_to_rest(workflow(graph))
        self.assertEqual('succeeded', run.status)
        self.assertEqual(30, run.context['vars']['count'])
        # 1 trigger + 30 passes of 2 nodes + the stop: more than one task's worth of nodes
        self.assertEqual(62, len(self.steps(run)))

    def test_loop_that_never_exits_should_stop_at_the_step_limit(self):
        graph = {
            'nodes': [
                {'id': 'trigger', 'type': 'trigger', 'config': {}},
                {'id': 'a', 'type': 'set_variables', 'config': {}},
                {'id': 'b', 'type': 'set_variables', 'config': {}},
            ],
            'edges': [
                {'id': 'e1', 'source': 'trigger', 'target': 'a', 'source_port': 'out'},
                {'id': 'e2', 'source': 'a', 'target': 'b', 'source_port': 'out'},
                {'id': 'e3', 'source': 'b', 'target': 'a', 'source_port': 'out'},
            ],
        }
        from app import app
        app.config['AI_WORKFLOWS_MAX_STEPS_PER_RUN'] = 40
        try:
            run = self.run_to_rest(workflow(graph))
        finally:
            app.config.pop('AI_WORKFLOWS_MAX_STEPS_PER_RUN')
        self.assertEqual('failed', run.status)
        self.assertIn('Step limit reached (40 steps)', run.error)
        self.assertEqual(40, len(self.steps(run)))

    def test_step_should_refuse_a_run_another_worker_holds(self):
        run = self.start(workflow(chain()))
        run.is_executing = True
        ai_workflows_engine_step(run.id)
        self.assertEqual([], self.steps(run))


class TestsActions(EngineTestCase):

    def test_allowlisted_write_should_execute(self):
        graph = chain(_action('note', _WRITE_TOOL, {'note_title': 'Triage', 'case_identifier': 1}))
        run = self.run_to_rest(workflow(graph, allowlist=[_WRITE_TOOL]))
        self.assertEqual('succeeded', run.status)
        self.assertEqual(1, len(self.executed))
        tool, arguments, mode = self.executed[0]
        self.assertEqual((_WRITE_TOOL, 'allowlisted_write'), (tool, mode))
        # Pinned to the run's case whatever the config says
        self.assertEqual(42, arguments['case_identifier'])
        self.assertEqual([], self.store.suggestions)

    def test_write_not_allowlisted_should_become_a_suggestion(self):
        graph = chain(_action('note', _WRITE_TOOL, {'note_title': 'Triage'}))
        run = self.run_to_rest(workflow(graph))
        self.assertEqual('succeeded', run.status)
        self.assertEqual([], self.executed)
        self.assertEqual(1, len(self.store.suggestions))
        suggestion = self.store.suggestions[0]
        self.assertEqual(_WRITE_TOOL, suggestion.proposed_action['tool'])
        self.assertEqual('suggested', self.store.tool_calls[0].execution_mode)
        self.assertFalse(self.steps(run)[-1].output['executed'])

    def test_read_tool_should_execute_without_allowlist(self):
        run = self.run_to_rest(workflow(chain(_action('get', _READ_TOOL))))
        self.assertEqual('succeeded', run.status)
        self.assertEqual('auto_read', self.executed[0][2])

    def test_dry_run_should_turn_allowlisted_writes_into_dry_run_suggestions(self):
        graph = chain(_action('note', _WRITE_TOOL, {'note_title': 'Triage'}))
        run = self.run_to_rest(workflow(graph, allowlist=[_WRITE_TOOL]), dry_run=True)
        self.assertEqual('succeeded', run.status)
        self.assertEqual([], self.executed)
        self.assertEqual(1, len(self.store.suggestions))
        self.assertTrue(self.store.suggestions[0].is_dry_run)
        self.assertIn('[dry run]', self.store.suggestions[0].title)

    def test_dry_run_question_should_leave_through_timeout(self):
        graph = chain({'id': 'ask', 'type': 'ask_analyst', 'config': {'question': 'Real?', 'fields': ['verdict']}},
                      {'id': 'after', 'type': 'stop', 'config': {}}, ports={'ask': 'timeout'})
        run = self.run_to_rest(workflow(graph), dry_run=True)
        self.assertEqual('succeeded', run.status)
        self.assertEqual([], list(self.store.waits.values()))
        self.assertEqual('timeout', self.steps(run)[1].port)


class TestsAgent(EngineTestCase):

    def test_agent_write_not_allowlisted_should_become_a_suggestion(self):
        provider = self.use_provider(tool_turn(_WRITE_TOOL, {'note_title': 'x'}), text_turn('Queued it.'))
        run = self.run_to_rest(workflow(chain(_agent('ai', 'Triage this', [_WRITE_TOOL]))))
        self.assertEqual('succeeded', run.status)
        self.assertEqual([], self.executed)
        self.assertEqual(1, len(self.store.suggestions))
        self.assertEqual(2, len(provider.calls))
        self.assertEqual(30, run.tokens_used)

    def test_agent_allowlisted_write_should_execute(self):
        self.use_provider(tool_turn(_WRITE_TOOL, {'note_title': 'x'}), text_turn('Done.'))
        run = self.run_to_rest(workflow(chain(_agent('ai', 'Triage this', [_WRITE_TOOL])),
                                        allowlist=[_WRITE_TOOL]))
        self.assertEqual('succeeded', run.status)
        self.assertEqual([(_WRITE_TOOL, {'note_title': 'x', 'case_identifier': 42}, 'allowlisted_write')],
                         self.executed)
        self.assertEqual([], self.store.suggestions)

    def test_agent_tool_outside_its_list_should_be_denied(self):
        self.use_provider(tool_turn(_WRITE_TOOL, {'note_title': 'x'}), text_turn('Ok.'))
        run = self.run_to_rest(workflow(chain(_agent('ai', 'Triage this')), allowlist=[_WRITE_TOOL]))
        self.assertEqual('succeeded', run.status)
        self.assertEqual([], self.executed)
        self.assertEqual('denied', self.store.tool_calls[0].execution_mode)

    def test_agent_should_never_see_a_secret_value(self):
        provider = self.use_provider(text_turn(f'The token is {SECRET_VALUE}'))
        prompt = f'Use {{{{ key("{SECRET_NAME}") }}}} to call the API'
        # The secret was resolved earlier in the run (so masking is armed)
        graph = chain({'id': 'vars', 'type': 'set_variables',
                       'config': {'variables': [{'name': 'tok', 'value': f'{{{{ key("{SECRET_NAME}") }}}}'}]}},
                      _agent('ai', prompt))
        run = self.run_to_rest(workflow(graph))
        self.assertEqual('succeeded', run.status)
        sent = json.dumps([m.content for m in provider.calls[0]['messages']], default=str)
        self.assertNotIn(SECRET_VALUE, sent)
        self.assertIn(f'[secret:{SECRET_NAME}]', sent)
        persisted = json.dumps([run.context, [(s.input, s.output, s.error) for s in self.steps(run)],
                                [(c.request_snapshot, c.response_snapshot) for c in self.store.llm_calls]],
                               default=str, ensure_ascii=False)
        self.assertNotIn(SECRET_VALUE, persisted)
        self.assertIn(MASK, persisted)

    def test_agent_provider_failure_should_fail_the_run(self):
        def _boom(*_args, **_kwargs):
            raise RuntimeError(f'refused {SECRET_VALUE}')
            yield  # pragma: no cover

        provider = self.use_provider()
        provider.stream_completion = _boom
        self.resolver.get(SECRET_NAME)
        run = self.run_to_rest(workflow(chain(_agent('ai', 'Triage'))))
        self.assertEqual('failed', run.status)
        self.assertNotIn(SECRET_VALUE, run.error)
        self.assertNotIn(SECRET_VALUE, self.steps(run)[-1].error)


class TestsSecrets(EngineTestCase):

    def test_secret_should_be_masked_in_variables_and_steps(self):
        graph = chain({'id': 'vars', 'type': 'set_variables',
                       'config': {'variables': [{'name': 'auth', 'value': f'Bearer {{{{ key("{SECRET_NAME}") }}}}'}]}})
        run = self.run_to_rest(workflow(graph))
        self.assertEqual('succeeded', run.status)
        # key() only reveals values inside http_request nodes
        self.assertEqual({'auth': f'Bearer [secret:{SECRET_NAME}]'}, run.context['vars'])
        persisted = json.dumps([run.context, [(s.input, s.output) for s in self.steps(run)]], default=str)
        self.assertNotIn(SECRET_VALUE, persisted)

    def test_secret_should_be_masked_in_tool_arguments(self):
        graph = chain(_action('note', _WRITE_TOOL, {'note_content': f'{{{{ key("{SECRET_NAME}") }}}}'}))
        run = self.run_to_rest(workflow(graph))
        self.assertEqual('succeeded', run.status)
        recorded = json.dumps([self.store.suggestions[0].proposed_action, self.store.tool_calls[0].arguments,
                               self.steps(run)[-1].input])
        self.assertNotIn(SECRET_VALUE, recorded)


class TestsWaits(EngineTestCase):

    def _ask_graph(self):
        return {
            'nodes': [
                {'id': 'trigger', 'type': 'trigger', 'config': {}},
                {'id': 'ask', 'type': 'ask_analyst', 'config': {'question': 'Real?', 'timeout_minutes': 30,
                                                                'fields': [{'name': 'verdict'}]}},
                {'id': 'answered', 'type': 'stop', 'config': {'reason': 'answered'}},
                {'id': 'late', 'type': 'stop', 'config': {'status': 'failed', 'reason': 'nobody answered'}},
            ],
            'edges': [
                {'id': 'e1', 'source': 'trigger', 'target': 'ask', 'source_port': 'out'},
                {'id': 'e2', 'source': 'ask', 'target': 'answered', 'source_port': 'answered'},
                {'id': 'e3', 'source': 'ask', 'target': 'late', 'source_port': 'timeout'},
            ],
        }

    def _parked(self):
        run = self.run_to_rest(workflow(self._ask_graph()))
        self.assertEqual('waiting', run.status)
        waits = list(self.store.waits.values())
        self.assertEqual(1, len(waits))
        return run, waits[0]

    def test_question_should_park_the_run_with_a_suggestion(self):
        run, wait = self._parked()
        self.assertEqual('user_input', wait.kind)
        self.assertEqual('ask', run.waiting_node_id)
        self.assertEqual(wait.suggestion_id, self.store.suggestions[0].id)
        self.assertEqual(wait.id, self.store.suggestions[0].wait_id)
        self.assertEqual('waiting', self.steps(run)[-1].status)

    def test_answer_should_resume_through_answered(self):
        run, wait = self._parked()
        ai_workflows_engine_resume_wait(wait, {'verdict': 'yes', 'answered_by': OWNER_ID}, resolved_by_id=OWNER_ID)
        self.assertEqual('resolved', wait.status)
        self.assertEqual('running', run.status)
        ai_workflows_engine_step(self.enqueued.pop())
        self.assertEqual('succeeded', run.status)
        resumed = self.steps(run)[2]
        self.assertEqual(('resumed', 'answered'), (resumed.status, resumed.port))
        self.assertEqual('yes', resumed.output['verdict'])

    def test_expired_wait_should_leave_through_timeout(self):
        run, wait = self._parked()
        ai_workflows_engine_expire_wait(wait)
        self.assertEqual('expired', wait.status)
        ai_workflows_engine_step(self.enqueued.pop())
        self.assertEqual('failed', run.status)
        self.assertEqual('nobody answered', run.error)
        self.assertEqual('timeout', self.steps(run)[2].port)
        self.assertTrue(self.steps(run)[2].output['timed_out'])

    def test_resolved_wait_should_not_resume_twice(self):
        run, wait = self._parked()
        ai_workflows_engine_resume_wait(wait, {'verdict': 'yes'})
        steps = len(self.steps(run))
        ai_workflows_engine_resume_wait(wait, {'verdict': 'no'})
        self.assertEqual(steps, len(self.steps(run)))

    def test_expired_delay_should_continue_through_out(self):
        graph = chain({'id': 'wait', 'type': 'delay', 'config': {'minutes': 10}},
                      {'id': 'end', 'type': 'stop', 'config': {}})
        run = self.run_to_rest(workflow(graph))
        self.assertEqual('waiting', run.status)
        wait = list(self.store.waits.values())[0]
        ai_workflows_engine_expire_wait(wait)
        ai_workflows_engine_step(self.enqueued.pop())
        self.assertEqual('succeeded', run.status)
        self.assertEqual('out', self.steps(run)[2].port)

    def test_cancel_should_close_the_waits(self):
        run, wait = self._parked()
        ai_workflows_engine_cancel_run(run, OWNER_ID, reason='Workflow deleted')
        self.assertEqual('cancelled', run.status)
        self.assertEqual('Workflow deleted', run.error)
        self.assertEqual('cancelled', wait.status)
        self.assertEqual(OWNER_ID, wait.resolved_by_id)


class TestsRecovery(EngineTestCase):

    def test_lost_node_should_fail_and_never_rerun(self):
        from app.iris_engine.ai_workflows.engine import _pop
        run = self.start(workflow(chain()))
        run.is_executing = True
        _pop(run.id)
        ai_workflows_engine_recover_stale(run)
        self.assertEqual('failed', run.status)
        self.assertEqual('failed', self.steps(run)[0].status)
        self.assertFalse(run.is_executing)
        self.assertEqual([run.id], self.enqueued)


class TestsHttpRequest(EngineTestCase):

    def _send(self, captured, body):
        def _send(request, **kwargs):
            captured.append((request, kwargs))
            return {'success': True, 'status_code': 202, 'response_headers': {}, 'response_body': body(request)}
        return patch('app.iris_engine.ai_workflows.nodes.webhooks_send', _send)

    def test_async_request_should_park_on_a_callback_without_persisting_credentials(self):
        captured = []
        graph = chain({'id': 'http', 'type': 'http_request', 'config': {
            'url': 'https://soar.example.org/jobs', 'mode': 'async', 'use_proxy': False, 'wait_timeout_minutes': 5,
            'headers': [{'name': 'X-Api-Key', 'value': f'{{{{ key("{SECRET_NAME}") }}}}', 'secret': True},
                        {'name': 'X-Echo', 'value': f'{{{{ key("{SECRET_NAME}") }}}}'}]}})
        # A careless remote echoes the request back
        with self._send(captured, lambda request: json.dumps({'body': request['body'].decode()})):
            run = self.run_to_rest(workflow(graph))
        self.assertEqual('waiting', run.status)
        request, kwargs = captured[0]
        self.assertEqual(SECRET_VALUE, request['headers']['X-Api-Key'])
        self.assertFalse(kwargs['follow_redirects'])
        self.assertFalse(kwargs['allow_private'])
        sent_body = json.loads(request['body'].decode())
        token = sent_body['callback']['token']
        self.assertTrue(sent_body['callback']['url'].endswith(
            f'/api/v2/ai-workflows/callbacks/{list(self.store.waits.values())[0].uuid}'))

        wait = list(self.store.waits.values())[0]
        self.assertEqual(('callback', 'pending'), (wait.kind, wait.status))
        from app.iris_engine.ai_workflows.engine import ai_workflows_engine_hash_token
        self.assertEqual(ai_workflows_engine_hash_token(token), wait.token_hash)
        persisted = json.dumps([run.context, [(s.input, s.output) for s in self.steps(run)]], default=str)
        self.assertNotIn(SECRET_VALUE, persisted)
        self.assertNotIn(token, persisted)

        ai_workflows_engine_resume_wait(wait, {'verdict': 'clean'}, source_ip='198.51.100.7')
        # Nothing is wired after the request: the run ends on the callback
        self.assertEqual([], self.enqueued)
        self.assertEqual('succeeded', run.status)
        resumed = self.steps(run)[-1]
        self.assertEqual(('resumed', 'out'), (resumed.status, resumed.port))
        self.assertEqual({'verdict': 'clean'}, resumed.output['payload'])
        self.assertEqual('198.51.100.7', wait.source_ip)

    def test_failed_request_should_leave_through_error_or_timeout(self):
        for error, port in (('Connection refused', 'error'), ('Timed out after 15s', 'timeout')):
            def _send(_request, **_kwargs):
                return {'success': False, 'status_code': None, 'error': error}
            graph = chain({'id': 'http', 'type': 'http_request', 'config': {'url': 'https://x.example.org'}},
                          {'id': 'end', 'type': 'stop', 'config': {}}, ports={'http': port})
            with patch('app.iris_engine.ai_workflows.nodes.webhooks_send', _send), \
                    patch('app.iris_engine.ai_workflows.nodes.webhooks_db_proxies', lambda: None):
                run = self.run_to_rest(workflow(graph))
            self.assertEqual('succeeded', run.status, error)
            self.assertEqual(port, self.steps(run)[1].port)
