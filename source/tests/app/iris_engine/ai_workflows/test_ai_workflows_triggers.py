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

"""Event and schedule triggers: which entity an event is about, and
the suppression rules (chain depth, dedup, active run, hourly cap) that
count a skipped trigger on the workflow instead of starting a run, and
the access refusal that records a minimal skipped run."""

import datetime
from unittest.mock import patch

from app.iris_engine.ai_workflows.triggers import ai_workflows_triggers_cron_tick
from app.iris_engine.ai_workflows.triggers import ai_workflows_triggers_entity
from app.iris_engine.ai_workflows.triggers import ai_workflows_triggers_process_event
from tests.app.iris_engine.ai_workflows.harness import OWNER_ID
from tests.app.iris_engine.ai_workflows.harness import EngineTestCase
from tests.app.iris_engine.ai_workflows.harness import chain
from tests.app.iris_engine.ai_workflows.harness import workflow

_TRIGGERS = 'app.iris_engine.ai_workflows.triggers'
_ENGINE = 'app.iris_engine.ai_workflows.engine'


def patch_skip_counter(test, workflows):
    """`ai_workflows_db_count_skip` on the workflow stubs of `workflows`
    (a dict, or a callable returning the stub)."""
    def _count(workflow_id, reason, now):
        wf = workflows(workflow_id) if callable(workflows) else workflows[workflow_id]
        wf.skipped_count = (wf.skipped_count or 0) + 1
        wf.last_skip_reason = reason[:64]
        wf.last_skipped_at = now

    patcher = patch(f'{_ENGINE}.ai_workflows_db_count_skip', _count)
    patcher.start()
    test.addCleanup(patcher.stop)


def _event(hook='on_postload_alert_create', object_type='alert', action='create', object_id=5, **extra):
    return {'event': hook, 'object_type': object_type, 'action': action, 'object_id': object_id,
            'case': None, 'data': {}, 'actor': {'id': 1}, **extra}


class TestsTriggerEntity(EngineTestCase):

    def test_alert_case_and_cluster_are_their_own_entity(self):
        self.assertEqual(('alert', 5, None), ai_workflows_triggers_entity(_event()))
        self.assertEqual(('case', 9, None), ai_workflows_triggers_entity(_event(object_type='case', object_id=9)))

    def test_war_room_sub_object(self):
        event = _event(object_type='war_room', action='task_create', object_id=3, data={'task_id': 12})
        self.assertEqual(('war_room', 3, {'type': 'war_room_task', 'id': 12}), ai_workflows_triggers_entity(event))
        self.assertEqual(('war_room', 3, None),
                         ai_workflows_triggers_entity(_event(object_type='war_room', action='update', object_id=3)))

    def test_case_object_goes_to_its_case(self):
        event = _event(object_type='ioc', object_id=77, case={'id': 4, 'name': 'c'})
        self.assertEqual(('case', 4, {'type': 'ioc', 'id': 77}), ai_workflows_triggers_entity(event))

    def test_run_completion_is_about_the_run_entity(self):
        event = _event(object_type='ai_workflow_run', object_id=8,
                       data={'id': 8, 'entity_type': 'alert', 'entity_id': 5})
        self.assertEqual(('alert', 5, {'type': 'ai_workflow_run', 'id': 8}), ai_workflows_triggers_entity(event))

    def test_unrelated_event_has_no_entity(self):
        self.assertEqual((None, None, None), ai_workflows_triggers_entity(_event(object_type='user')))


class TestsEventTrigger(EngineTestCase):

    def setUp(self):
        super().setUp()
        self.workflows = {}
        self.dedup_hit = None
        self.active_run = None
        self.runs_last_hour = 0
        for target, replacement in (
                (f'{_TRIGGERS}.ai_workflows_db_get', self.workflows.get),
                (f'{_TRIGGERS}.ai_workflows_db_lock_workflow', self.workflows.get),
                (f'{_TRIGGERS}.ai_workflows_db_commit', lambda: None),
                (f'{_TRIGGERS}.ai_workflows_db_recent_dedup_run', lambda *_args: self.dedup_hit),
                (f'{_TRIGGERS}.ai_workflows_db_active_run_for_entity', lambda *_args: self.active_run),
                (f'{_TRIGGERS}.ai_workflows_db_count_runs_since', lambda *_args: self.runs_last_hour),
                (f'{_TRIGGERS}.ai_workflows_db_rollback', lambda: None)):
            patcher = patch(target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        patch_skip_counter(self, self.workflows)

    def _workflow(self, **trigger_config):
        config = {'hooks': ['on_postload_alert_create']}
        config.update(trigger_config)
        wf = workflow(chain(), trigger_type='event', trigger_config=config, skipped_count=0, last_skip_reason=None,
                      last_skipped_at=None)
        self.workflows[wf.id] = wf
        return wf

    def _fire(self, wf, event=None, chain_depth=None):
        run_ids = ai_workflows_triggers_process_event(event or _event(), [wf.id], chain_depth=chain_depth,
                                                      parent_run_id=99 if chain_depth is not None else None)
        return [self.store.runs[run_id] for run_id in run_ids]

    def test_matching_event_should_start_a_run(self):
        runs = self._fire(self._workflow())
        self.assertEqual(['running'], [r.status for r in runs])
        run = runs[0]
        self.assertEqual(('alert', 5, OWNER_ID, 0), (run.entity_type, run.entity_id, run.run_as_user_id,
                                                     run.chain_depth))
        self.assertEqual('11:alert:5', run.dedup_key)
        self.assertEqual('on_postload_alert_create', run.context['trigger']['hook'])
        self.assertEqual([run.id], self.enqueued)

    def test_other_hook_or_false_condition_should_not_start(self):
        self.assertEqual([], self._fire(self._workflow(hooks=['on_postload_case_create'])))
        self.assertEqual([], self._fire(self._workflow(condition='{{ entity_id == 6 }}')))
        self.assertEqual(1, len(self._fire(self._workflow(condition='{{ entity_id == 5 }}'))))

    def test_wildcard_hook_should_match(self):
        self.assertEqual(1, len(self._fire(self._workflow(hooks=['*']))))

    def test_chain_depth_over_the_limit_should_skip(self):
        wf = self._workflow()
        runs = self._fire(wf, chain_depth=1)
        self.assertEqual(('running', 2, 99), (runs[0].status, runs[0].chain_depth, runs[0].parent_run_id))
        self.assertEqual([], self._fire(wf, chain_depth=2))
        self.assertEqual((1, 'Chain depth limit reached'), (wf.skipped_count, wf.last_skip_reason))

    def test_duplicate_within_the_window_should_skip(self):
        wf = self._workflow(dedup_minutes=30)
        self.dedup_hit = object()
        self.assertEqual([], self._fire(wf))
        self.assertEqual((1, 'Duplicate within the dedup window'), (wf.skipped_count, wf.last_skip_reason))
        self.assertIsNotNone(wf.last_skipped_at)
        self.assertEqual({}, self.store.runs)
        self.assertEqual([], self.enqueued)

    def test_dedup_without_window_should_not_apply(self):
        self.dedup_hit = object()
        self.assertEqual('running', self._fire(self._workflow())[0].status)

    def test_active_run_on_the_entity_should_skip_unless_allowed(self):
        self.active_run = object()
        wf = self._workflow()
        self.assertEqual([], self._fire(wf))
        self.assertEqual('A run is still active on the entity', wf.last_skip_reason)
        self.assertEqual('running', self._fire(self._workflow(skip_if_active=False))[0].status)

    def test_hourly_cap_should_skip(self):
        wf = self._workflow()
        wf.max_runs_per_hour = 3
        self.runs_last_hour = 3
        self.assertEqual([], self._fire(wf))
        self.assertEqual((1, 'Hourly run limit reached'), (wf.skipped_count, wf.last_skip_reason))

    def test_access_denied_should_skip(self):
        with patch('app.iris_engine.ai_workflows.engine.ai_workflows_entities_user_can_access',
                   lambda _user, _type, _id: False):
            runs = self._fire(self._workflow())
        self.assertEqual('skipped', runs[0].status)
        self.assertIn('cannot access alert #5', runs[0].error)
        # A minimal row: nothing about the trigger is kept
        self.assertEqual(({}, None, {}), (runs[0].definition_snapshot, runs[0].trigger_payload, runs[0].context))
        self.assertEqual([], self.enqueued)

    def test_inactive_workflow_should_not_start(self):
        wf = self._workflow()
        wf.is_active = False
        self.assertEqual([], self._fire(wf))


class TestsCronTrigger(EngineTestCase):

    def setUp(self):
        super().setUp()
        self.wf = workflow(chain(), trigger_type='cron', trigger_config={'cron': '*/5 * * * *', 'target': 'cases'})
        for target, replacement in (
                (f'{_TRIGGERS}.ai_workflows_db_list_active', lambda _type: [self.wf]),
                (f'{_TRIGGERS}.ai_workflows_db_lock_workflow', lambda _id: self.wf),
                (f'{_TRIGGERS}.ai_workflows_db_open_case_ids',
                 lambda _limit, _scope, after_id=None: [1, 2] if after_id is None else []),
                (f'{_TRIGGERS}.ai_workflows_entities_filter_accessible', lambda _user, _type, ids: list(ids)),
                (f'{_TRIGGERS}.ai_workflows_db_commit', lambda: None),
                (f'{_TRIGGERS}.ai_workflows_db_active_run_for_entity', lambda *_args: None)):
            patcher = patch(target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_due_schedule_should_start_one_run_per_target_once(self):
        now = datetime.datetime(2026, 10, 5, 10, 15, 12)
        run_ids = ai_workflows_triggers_cron_tick(now)
        runs = [self.store.runs[i] for i in run_ids]
        self.assertEqual([('case', 1), ('case', 2)], [(r.entity_type, r.entity_id) for r in runs])
        self.assertEqual('cron', runs[0].trigger_type)
        self.assertEqual(now.replace(second=0), self.wf.last_fired_at)
        self.assertEqual([], ai_workflows_triggers_cron_tick(now))

    def test_not_due_schedule_should_not_start(self):
        self.assertEqual([], ai_workflows_triggers_cron_tick(datetime.datetime(2026, 10, 5, 10, 16)))
        self.assertIsNone(self.wf.last_fired_at)
