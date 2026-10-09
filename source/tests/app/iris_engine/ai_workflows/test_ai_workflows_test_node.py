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

"""Testing one node on its own: the run holds that node definition only,
starts at it with the context supplied (or the one an earlier event
saw), stops after it, and is not an event of the workflow."""

from unittest.mock import patch

from app.iris_engine.ai_workflows.engine import _publish_complete
from app.iris_engine.ai_workflows.engine import ai_workflows_engine_replay_context
from app.iris_engine.ai_workflows.engine import ai_workflows_engine_step
from app.iris_engine.ai_workflows.engine import ai_workflows_engine_test_context
from app.iris_engine.ai_workflows.engine import ai_workflows_engine_test_node
from app.iris_engine.ai_workflows.graph import ai_workflows_graph_validate_node
from app.iris_engine.ai_workflows.sandbox import ai_workflows_sandbox_run
from tests.app.iris_engine.ai_workflows.harness import OWNER_ID
from tests.app.iris_engine.ai_workflows.harness import EngineTestCase
from tests.app.iris_engine.ai_workflows.harness import chain
from tests.app.iris_engine.ai_workflows.harness import workflow


def _in_process(source, variables, max_steps=None, timeout_seconds=None):
    return ai_workflows_sandbox_run(source, variables, max_steps=max_steps, timeout_seconds=timeout_seconds)


def _python(code, node_id='py'):
    return {'id': node_id, 'type': 'python',
            'config': {'code': code, 'inputs': [{'name': 'factor', 'value': '{{ vars.factor }}'},
                                                {'name': 'title', 'value': '{{ entity.title }}'}]}}


class TestsTestNode(EngineTestCase):

    def setUp(self):
        super().setUp()
        patcher = patch('app.iris_engine.ai_workflows.nodes.ai_workflows_sandbox_execute', _in_process)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _drive(self, run):
        for _ in range(10):
            if run.id not in self.enqueued:
                break
            self.enqueued.remove(run.id)
            ai_workflows_engine_step(run.id)
        return run

    def _test(self, wf, node, context, **kwargs):
        return ai_workflows_engine_test_node(wf, node, context, trigger_type='manual', triggered_by_user_id=OWNER_ID,
                                             **kwargs)

    def test_context_should_hold_the_entity_and_the_supplied_parts(self):
        context = ai_workflows_engine_test_context('manual', {'event': 'x'}, 'case', 42,
                                                   {'up': {'output': {'a': 1}, 'port': 'success'}}, {'factor': '2'})
        self.assertEqual('Phishing', context['entity']['title'])
        self.assertEqual({'factor': '2'}, context['vars'])
        self.assertEqual({'a': 1}, context['nodes']['up']['output'])
        self.assertEqual('x', context['trigger']['hook'])

    def test_node_should_run_alone_with_the_context(self):
        wf = workflow(chain(_python('result = 0')))
        node = _python("result = {'n': int(inputs['factor']) * 2, 't': inputs['title']}")
        run = self._test(wf, node, ai_workflows_engine_test_context('manual', None, 'case', 42, None,
                                                                    {'factor': '4'}),
                         entity_type='case', entity_id=42)
        self.assertEqual('py', run.tested_node_id)
        self.assertEqual([node], run.definition_snapshot['graph']['nodes'])
        self.assertEqual([], run.definition_snapshot['graph']['edges'])
        self._drive(run)
        self.assertEqual('succeeded', run.status, run.error)
        self.assertEqual(['py'], [s.node_id for s in self.steps(run)])
        self.assertEqual({'n': 8, 't': 'Phishing'}, run.context['nodes']['py']['output']['result'])

    def test_node_should_stop_after_itself(self):
        wf = workflow(chain(_python('result = 1'), {'id': 'next', 'type': 'python', 'config': {'code': 'fail()'}}))
        run = self._drive(self._test(wf, _python('result = 1'), ai_workflows_engine_test_context(
            'manual', None, None, None)))
        self.assertEqual('succeeded', run.status, run.error)
        self.assertEqual(['py'], [s.node_id for s in self.steps(run)])

    def test_node_should_start_from_an_earlier_event(self):
        source = self.run_to_rest(workflow(chain(
            {'id': 'vars', 'type': 'set_variables', 'config': {'variables': [{'name': 'factor', 'value': '5'}]}},
            _python("fail('broken')"))))
        steps = self.steps(source)
        context = ai_workflows_engine_replay_context(source, steps, steps[-1])
        run = self._drive(self._test(workflow(chain(_python('result = 0'))),
                                     _python("result = int(inputs['factor'])"), context,
                                     entity_type=source.entity_type, entity_id=source.entity_id,
                                     source_step_id=steps[-1].id))
        self.assertEqual('succeeded', run.status, run.error)
        self.assertEqual(steps[-1].id, run.replayed_from_step_id)
        self.assertEqual(5, run.context['nodes']['py']['output']['result'])

    def test_node_should_not_publish_completion(self):
        run = self._drive(self._test(workflow(chain(_python('result = 1'))), _python('result = 1'),
                                     ai_workflows_engine_test_context('manual', None, None, None)))
        with patch('app.iris_engine.module_handler.module_handler.call_modules_hook') as hook:
            _publish_complete(run)
        hook.assert_not_called()

    def test_validate_node_should_check_the_allowlist(self):
        action = {'id': 'act', 'type': 'action', 'config': {'tool': 'iris_cases_create', 'arguments': {}}}
        self.assertTrue(ai_workflows_graph_validate_node(action, []))
        self.assertEqual([], ai_workflows_graph_validate_node(action, ['iris_cases_create']))

    def test_validate_node_should_reject_malformed_nodes(self):
        self.assertTrue(ai_workflows_graph_validate_node('nope', []))
        self.assertTrue(ai_workflows_graph_validate_node({'id': 'bad id!', 'type': 'python'}, []))
        self.assertTrue(ai_workflows_graph_validate_node({'id': 'x', 'type': 'unknown'}, []))
        self.assertTrue(ai_workflows_graph_validate_node({'id': 'x', 'type': 'trigger'}, []))
