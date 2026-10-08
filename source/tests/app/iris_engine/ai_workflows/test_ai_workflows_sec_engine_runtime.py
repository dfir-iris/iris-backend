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

"""Executing runs: access re-checked before every node (resumes
included), recovered steps never overwritten, crashes fail the run,
heartbeats, requeue stamps, the run-then-wait lock order, the tick and
the batched prune."""

import datetime
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock
from unittest.mock import patch

from app.datamgmt.ai_workflows import ai_workflows_db
from app.iris_engine.ai_workflows import tasks
from app.iris_engine.ai_workflows.engine import ai_workflows_engine_lock_run_then_wait
from app.iris_engine.ai_workflows.engine import ai_workflows_engine_recover_stale
from app.iris_engine.ai_workflows.engine import ai_workflows_engine_requeue
from app.iris_engine.ai_workflows.engine import ai_workflows_engine_resume_wait
from app.iris_engine.ai_workflows.engine import ai_workflows_engine_step
from app.iris_engine.ai_workflows.nodes import NodeResult
from app.models.ai_workflows import AiWorkflowWait
from app.models.ai_workflows import WAIT_PENDING
from app.models.ai_workflows import WAIT_RESOLVED
from tests.app.iris_engine.ai_workflows.harness import SECRET_NAME
from tests.app.iris_engine.ai_workflows.harness import EngineTestCase
from tests.app.iris_engine.ai_workflows.harness import chain
from tests.app.iris_engine.ai_workflows.harness import workflow

_ENGINE = 'app.iris_engine.ai_workflows.engine'
_TASKS = 'app.iris_engine.ai_workflows.tasks'


def _vars_node(node_id):
    return {'id': node_id, 'type': 'set_variables', 'config': {'variables': [{'name': node_id, 'value': '1'}]}}


def _patch(test, target, replacement):
    patcher = patch(target, replacement)
    started = patcher.start()
    test.addCleanup(patcher.stop)
    return started


class TestsAccessLost(EngineTestCase):

    def setUp(self):
        super().setUp()
        self.access = []
        _patch(self, f'{_ENGINE}.ai_workflows_entities_user_can_access',
               lambda _user, _type, _id: self.access.pop(0) if self.access else True)

    def test_access_lost_between_nodes_should_fail_the_run(self):
        # start, trigger, a: granted; b: lost
        self.access = [True, True, True, False]
        run = self.run_to_rest(workflow(chain(_vars_node('a'), _vars_node('b'))))
        self.assertEqual('failed', run.status)
        self.assertIn('Access to case #42 lost', run.error)
        self.assertEqual(['trigger', 'a'], [s.node_id for s in self.steps(run)])
        self.assertEqual([run], self.published)

    def test_access_lost_while_waiting_should_fail_the_resumed_branch(self):
        graph = chain({'id': 'pause', 'type': 'delay', 'config': {'minutes': 5}}, _vars_node('after'))
        run = self.run_to_rest(workflow(graph))
        self.assertEqual('waiting', run.status)
        wait = self.store.pending_waits(run.id)[0]
        ai_workflows_engine_resume_wait(wait, None)
        self.access = [False]
        self.enqueued.remove(run.id)
        ai_workflows_engine_step(run.id)
        self.assertEqual('failed', run.status)
        self.assertIn('lost', run.error)
        self.assertNotIn('after', [s.node_id for s in self.steps(run)])

    def test_key_scope_should_be_rechecked_before_each_node(self):
        wf = workflow(chain(_vars_node('a')))
        run = self.start(wf, entity_type='alert', entity_id=5)
        run.scope_mask = 0x8000  # war rooms only: the alert is out of the key scope
        self.enqueued.remove(run.id)
        ai_workflows_engine_step(run.id)
        self.assertEqual('failed', run.status)
        self.assertIn('Access to alert #5 lost', run.error)
        self.assertEqual([], self.steps(run))


class TestsNodeBookkeeping(EngineTestCase):

    def _run_with(self, node_fn, graph=None):
        _patch(self, f'{_ENGINE}.ai_workflows_nodes_execute', node_fn)
        return self.run_to_rest(workflow(graph or chain(_vars_node('a'))))

    def test_recovered_step_should_not_be_overwritten(self):
        def _node(ctx, node):
            if node['id'] == 'a':
                # The tick recovered the run while this node was executing
                ctx.step.status = 'failed'
                ctx.step.error = 'The worker executing this node stopped'
                ctx.run.status = 'failed'
                ctx.run.error = 'recovered'
            return NodeResult(output={'late': True})

        run = self._run_with(_node)
        step = self.steps(run)[-1]
        self.assertEqual(('failed', 'The worker executing this node stopped', None),
                         (step.status, step.error, step.output))
        self.assertEqual(('failed', 'recovered'), (run.status, run.error))
        self.assertNotIn('a', run.context.get('nodes', {}))

    def test_cancelled_run_should_only_record_the_step(self):
        def _node(ctx, node):
            if node['id'] != 'a':
                return NodeResult(output={'n': node['id']})
            ctx.run.status = 'cancelled'
            return NodeResult(output={'n': node['id']}, context_updates={'vars': {'x': 1}})

        graph = chain(_vars_node('a'), _vars_node('b'))
        run = self._run_with(_node, graph)
        self.assertEqual('cancelled', run.status)
        self.assertEqual(['succeeded', 'succeeded'], [s.status for s in self.steps(run)])
        self.assertNotIn('a', run.context.get('nodes', {}))
        self.assertNotEqual({'x': 1}, run.context.get('vars'))
        self.assertEqual([], run.pending_nodes)

    def test_used_keys_should_be_kept_on_the_run(self):
        def _node(ctx, node):
            if node['id'] == 'a':
                ctx.resolver.get(SECRET_NAME)
            return NodeResult(output={})

        run = self._run_with(_node)
        self.assertEqual('succeeded', run.status)
        self.assertEqual([SECRET_NAME], run.used_key_names)

    def test_crash_after_the_node_ran_should_fail_the_step_and_the_run(self):
        def _boom(*_args, **_kwargs):
            raise RuntimeError('bookkeeping')

        def _rollback():
            # What the database does: the uncommitted step update is lost
            for step in self.store.steps.values():
                if step.status == 'succeeded':
                    step.status = 'running'

        with patch(f'{_ENGINE}._store_node', _boom), patch(f'{_ENGINE}.ai_workflows_db_rollback', _rollback):
            run = self.run_to_rest(workflow(chain(_vars_node('a'))))
        self.assertEqual('failed', run.status)
        self.assertIn('Recording node', run.error)
        self.assertEqual(['failed'], [s.status for s in self.steps(run)])
        self.assertFalse(run.is_executing)
        self.assertEqual([run], self.published)

    def test_engine_crash_should_fail_the_run(self):
        def _boom(_run_id):
            raise RuntimeError('db gone')

        with patch(f'{_ENGINE}.ai_workflows_db_next_step_seq', _boom):
            run = self.run_to_rest(workflow(chain(_vars_node('a'))))
        self.assertEqual('failed', run.status)
        self.assertIn('The workflow engine failed', run.error)
        self.assertFalse(run.is_executing)


class TestsPreparedWait(EngineTestCase):
    """An async HTTP node commits its callback wait before sending."""

    def _run(self, early_callback):
        def _node(ctx, node):
            if node['id'] != 'call':
                return NodeResult(output={'n': node['id']})
            wait = self.store.add(AiWorkflowWait(uuid=uuid.uuid4(), run_id=ctx.run.id, node_id='call',
                                                 kind='callback', token_hash='h', status=WAIT_PENDING,
                                                 expires_at=datetime.datetime(2099, 1, 1)))
            if early_callback:
                ai_workflows_engine_resume_wait(wait, {'verdict': 'malicious'})
            return NodeResult(output={'status_code': 202}, wait={'kind': 'callback', 'uuid': wait.uuid,
                                                                  'token_hash': 'h', 'wait_id': wait.id})

        _patch(self, f'{_ENGINE}.ai_workflows_nodes_execute', _node)
        graph = chain({'id': 'call', 'type': 'http_request', 'config': {}}, _vars_node('after'))
        return self.run_to_rest(workflow(graph))

    def test_prepared_wait_should_be_adopted(self):
        run = self._run(early_callback=False)
        self.assertEqual('waiting', run.status)
        self.assertEqual(1, len(self.store.waits))
        self.assertEqual('call', run.waiting_node_id)
        self.assertEqual('waiting', self.steps(run)[-1].status)

    def test_early_callback_should_continue_the_branch(self):
        run = self._run(early_callback=True)
        self.assertEqual('succeeded', run.status)
        self.assertEqual(1, len(self.store.waits))
        self.assertEqual(WAIT_RESOLVED, next(iter(self.store.waits.values())).status)
        output = run.context['nodes']['call']['output']
        self.assertEqual(({'verdict': 'malicious'}, 202), (output['payload'], output['status_code']))
        self.assertIsNone(run.waiting_node_id)
        self.assertIn('after', [s.node_id for s in self.steps(run)])


class TestsLiveness(EngineTestCase):

    def test_recovery_should_spare_a_run_with_a_recent_heartbeat(self):
        run = self.start(workflow(chain(_vars_node('a'))))
        before = datetime.datetime(2026, 10, 8, 12, 0)
        run.is_executing = True
        run.executing_since = before + datetime.timedelta(minutes=1)
        ai_workflows_engine_recover_stale(run, before)
        self.assertTrue(run.is_executing)
        run.executing_since = before - datetime.timedelta(minutes=1)
        ai_workflows_engine_recover_stale(run, before)
        self.assertFalse(run.is_executing)

    def test_requeue_should_stamp_the_run(self):
        run = self.start(workflow(chain()))
        self.enqueued.clear()
        with patch(f'{_ENGINE}.ai_workflows_db_mark_requeued', MagicMock()) as mark:
            ai_workflows_engine_requeue(SimpleNamespace(id=run.id, uuid=run.uuid))
        self.assertEqual(run.id, mark.call_args.args[0])
        self.assertEqual([run.id], self.enqueued)


class TestsLockOrder(EngineTestCase):

    def test_run_should_be_locked_before_its_wait(self):
        calls = []
        wait = SimpleNamespace(id=5, run_id=9, status=WAIT_PENDING)
        run = SimpleNamespace(id=9)

        def _get_wait(wait_id, lock=False):
            calls.append(('wait', lock))
            return wait if wait_id == 5 else None

        def _get_run(run_id, lock=False):
            calls.append(('run', lock))
            return run

        with patch(f'{_ENGINE}.ai_workflows_db_get_wait', _get_wait), \
                patch(f'{_ENGINE}.ai_workflows_db_get_run', _get_run), \
                patch(f'{_ENGINE}.ai_workflows_db_get_wait_by_uuid', lambda _uuid: wait):
            self.assertEqual((run, wait), ai_workflows_engine_lock_run_then_wait(5))
            self.assertEqual([('wait', False), ('run', True), ('wait', True)], calls)
            calls.clear()
            self.assertEqual((run, wait), ai_workflows_engine_lock_run_then_wait(wait_uuid='u'))
            self.assertEqual([('run', True), ('wait', True)], calls)
            self.assertEqual((None, None), ai_workflows_engine_lock_run_then_wait(6))


class TestsTick(EngineTestCase):

    def test_expiry_should_lock_the_run_then_the_wait(self):
        pending = SimpleNamespace(id=5, status=WAIT_PENDING)
        resolved = SimpleNamespace(id=6, status=WAIT_RESOLVED)
        locks = {5: (SimpleNamespace(id=1), pending), 6: (SimpleNamespace(id=2), resolved)}
        _patch(self, f'{_TASKS}.ai_workflows_db_expired_waits', lambda _now: [SimpleNamespace(id=5),
                                                                              SimpleNamespace(id=6)])
        lock = _patch(self, f'{_TASKS}.ai_workflows_engine_lock_run_then_wait', MagicMock(side_effect=locks.get))
        expire = _patch(self, f'{_TASKS}.ai_workflows_engine_expire_wait', MagicMock())
        _patch(self, f'{_TASKS}.ai_workflows_db_commit', lambda: None)
        self.assertEqual(1, tasks._expire_waits(datetime.datetime(2026, 10, 8)))
        self.assertEqual([5, 6], [c.args[0] for c in lock.call_args_list])
        expire.assert_called_once_with(pending)

    def test_recovery_should_requeue_with_a_stamp_and_survive_failures(self):
        stale = [SimpleNamespace(id=1, uuid='a'), SimpleNamespace(id=2, uuid='b')]
        stuck = [SimpleNamespace(id=3, uuid='c'), SimpleNamespace(id=4, uuid='d')]
        _patch(self, f'{_TASKS}.ai_workflows_db_stale_executing_runs', lambda _before: stale)
        _patch(self, f'{_TASKS}.ai_workflows_db_stuck_running_runs', lambda _before: stuck)
        _patch(self, f'{_TASKS}.ai_workflows_db_rollback', lambda: None)
        recover = _patch(self, f'{_TASKS}.ai_workflows_engine_recover_stale',
                         MagicMock(side_effect=[RuntimeError('x'), None]))
        requeue = _patch(self, f'{_TASKS}.ai_workflows_engine_requeue', MagicMock(side_effect=[RuntimeError('y'), None]))
        now = datetime.datetime(2026, 10, 8, 12, 0)
        self.assertEqual({'stale': 1, 'requeued': 1}, tasks._recover(now))
        self.assertEqual(now - tasks._STALE_EXECUTING, recover.call_args.args[1])
        self.assertEqual([3, 4], [c.args[0].id for c in requeue.call_args_list])


class _FakeSession:

    def __init__(self, rowcounts):
        self.rowcounts = list(rowcounts)
        self.statements = []
        self.commits = 0

    def execute(self, statement):
        self.statements.append(statement)
        return SimpleNamespace(rowcount=self.rowcounts.pop(0) if self.rowcounts else 0)

    def commit(self):
        self.commits += 1


class TestsPrune(EngineTestCase):

    def test_prune_should_delete_in_committed_batches(self):
        batch = ai_workflows_db._PRUNE_BATCH
        session = _FakeSession([batch, batch, 3])
        with patch.object(ai_workflows_db, 'db', SimpleNamespace(session=session)):
            counts = ai_workflows_db.ai_workflows_db_prune(datetime.datetime(2026, 1, 1))
        self.assertEqual(2 * batch + 3, counts['runs'])
        self.assertEqual({'runs', 'tool_calls', 'llm_calls', 'suggestions', 'inbound_events', 'versions'},
                         set(counts))
        # One commit per batch: 3 for the runs, 1 for each other table
        self.assertEqual(len(session.statements), session.commits)
        self.assertEqual(8, session.commits)
        for statement in session.statements:
            self.assertIn('LIMIT', str(statement.compile()).upper())

    def test_prune_should_keep_the_newest_versions(self):
        sql = str(ai_workflows_db._old_version_ids().compile()).lower()
        self.assertIn('row_number() over (partition by', sql)
        self.assertIn('rank >', sql)

    def test_prune_should_include_orphan_calls(self):
        session = _FakeSession([])
        with patch.object(ai_workflows_db, 'db', SimpleNamespace(session=session)):
            ai_workflows_db.ai_workflows_db_prune(datetime.datetime(2026, 1, 1))
        tables = [str(s.compile()).split()[2] for s in session.statements]
        self.assertEqual(['ai_workflow_run', 'ai_workflow_tool_call', 'ai_workflow_llm_call', 'ai_suggestion',
                          'ai_workflow_inbound_event', 'ai_workflow_version'], tables)
        orphan_calls = [str(s.compile()) for s in session.statements[1:3]]
        for sql in orphan_calls:
            self.assertIn('run_id IS NULL', sql)
