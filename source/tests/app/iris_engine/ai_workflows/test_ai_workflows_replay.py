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

"""Replaying an event a node processed: the context it saw is rebuilt
from the steps recorded before it, and a new run of the current
definition starts at that node."""

from unittest.mock import patch

from app.iris_engine.ai_workflows.engine import ai_workflows_engine_replay_context
from app.iris_engine.ai_workflows.engine import ai_workflows_engine_replay_step
from app.iris_engine.ai_workflows.engine import ai_workflows_engine_step
from app.iris_engine.ai_workflows.sandbox import ai_workflows_sandbox_run
from tests.app.iris_engine.ai_workflows.harness import OWNER_ID
from tests.app.iris_engine.ai_workflows.harness import EngineTestCase
from tests.app.iris_engine.ai_workflows.harness import chain
from tests.app.iris_engine.ai_workflows.harness import workflow


def _in_process(source, variables, max_steps=None, timeout_seconds=None):
    return ai_workflows_sandbox_run(source, variables, max_steps=max_steps, timeout_seconds=timeout_seconds)


def _graph(code):
    return chain({'id': 'vars', 'type': 'set_variables',
                  'config': {'variables': [{'name': 'factor', 'value': '3'}]}},
                 {'id': 'py', 'type': 'python',
                  'config': {'code': code, 'inputs': [{'name': 'factor', 'value': '{{ vars.factor }}'}]}})


class TestsReplay(EngineTestCase):

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

    def _source(self):
        source = self.run_to_rest(workflow(_graph("fail('broken')")))
        self.assertEqual('failed', source.status)
        return source, self.steps(source)

    def test_context_should_hold_what_the_step_saw(self):
        source, steps = self._source()
        failed = steps[-1]
        context = ai_workflows_engine_replay_context(source, steps, failed)
        self.assertEqual({'factor': '3'}, context['vars'])
        self.assertEqual(['trigger', 'vars'], sorted(context['nodes']))
        self.assertEqual('vars', context['last'])
        self.assertEqual('Phishing', context['entity']['title'])
        self.assertEqual(source.context['trigger'], context['trigger'])
        self.assertEqual({'run_uuid': str(source.uuid) if source.uuid else None, 'step_id': failed.id,
                          'node_id': 'py'}, context['replay'])

    def test_context_of_the_first_step_should_have_no_node(self):
        source, steps = self._source()
        context = ai_workflows_engine_replay_context(source, steps, steps[0])
        self.assertEqual(({}, {}), (context['nodes'], context['vars']))
        self.assertNotIn('last', context)

    def test_replay_should_run_the_current_definition_from_the_node(self):
        source, steps = self._source()
        fixed = workflow(_graph("result = int(inputs['factor']) * 2"), version=2)
        replay = ai_workflows_engine_replay_step(fixed, source, steps[-1], steps, triggered_by_user_id=OWNER_ID)
        self.assertEqual(['py'], replay.pending_nodes)
        self.assertEqual(steps[-1].id, replay.replayed_from_step_id)
        self._drive(replay)
        self.assertEqual('succeeded', replay.status, replay.error)
        self.assertEqual(['py'], [s.node_id for s in self.steps(replay)])
        self.assertEqual(6, replay.context['nodes']['py']['output']['result'])
        self.assertEqual((source.entity_type, source.entity_id), (replay.entity_type, replay.entity_id))

    def test_replay_should_keep_the_dry_run_flag_it_is_given(self):
        source, steps = self._source()
        replay = ai_workflows_engine_replay_step(workflow(_graph('result = 1')), source, steps[-1], steps,
                                                 triggered_by_user_id=OWNER_ID, dry_run=True)
        self.assertTrue(replay.is_dry_run)
