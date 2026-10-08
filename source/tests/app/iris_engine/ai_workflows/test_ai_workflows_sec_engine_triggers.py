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

"""Triggers: access is checked before the condition, suppressed
triggers only count on the workflow, the workflow lock covers the checks
and the insert, the '*' wildcard ignores the workflow engine's own
hooks, the hook listener reads a cached id/hooks list, and schedules
only target entities the owner can access."""

import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock
from unittest.mock import patch

from app.iris_engine.ai_workflows import triggers
from app.iris_engine.ai_workflows.engine import ai_workflows_engine_skip_run
from app.iris_engine.ai_workflows.triggers import ai_workflows_triggers_cron_tick
from app.iris_engine.ai_workflows.triggers import ai_workflows_triggers_process_event
from tests.app.iris_engine.ai_workflows.harness import OWNER_ID
from tests.app.iris_engine.ai_workflows.harness import EngineTestCase
from tests.app.iris_engine.ai_workflows.harness import chain
from tests.app.iris_engine.ai_workflows.harness import workflow
from tests.app.iris_engine.ai_workflows.test_ai_workflows_triggers import patch_skip_counter

_TRIGGERS = 'app.iris_engine.ai_workflows.triggers'
_ENGINE = 'app.iris_engine.ai_workflows.engine'


def _event(object_id=5):
    return {'event': 'on_postload_alert_create', 'object_type': 'alert', 'action': 'create', 'object_id': object_id,
            'case': None, 'data': {}, 'actor': {'id': 1}}


def _patch(test, target, replacement):
    patcher = patch(target, replacement)
    started = patcher.start()
    test.addCleanup(patcher.stop)
    return started


class _TriggerCase(EngineTestCase):

    def setUp(self):
        super().setUp()
        self.workflows = {}
        self.calls = []
        self.dedup_hit = None
        self.can_access = True

        def _record(name, value=None):
            def _call(*_args, **_kwargs):
                self.calls.append(name)
                return value() if callable(value) else value
            return _call

        def _lock(workflow_id):
            self.calls.append('lock')
            return self.workflows.get(workflow_id)

        def _access(_user, _type, _id):
            self.calls.append('access')
            return self.can_access

        def _add(obj):
            self.calls.append('insert')
            return self.store.add(obj)

        _patch(self, f'{_TRIGGERS}.ai_workflows_db_get', self.workflows.get)
        _patch(self, f'{_TRIGGERS}.ai_workflows_db_lock_workflow', _lock)
        _patch(self, f'{_TRIGGERS}.ai_workflows_db_commit', _record('commit'))
        _patch(self, f'{_TRIGGERS}.ai_workflows_db_rollback', _record('rollback'))
        _patch(self, f'{_TRIGGERS}.ai_workflows_db_recent_dedup_run', _record('dedup', lambda: self.dedup_hit))
        _patch(self, f'{_TRIGGERS}.ai_workflows_db_active_run_for_entity', _record('active'))
        _patch(self, f'{_TRIGGERS}.ai_workflows_db_count_runs_since', _record('count', 0))
        _patch(self, f'{_ENGINE}.ai_workflows_entities_user_can_access', _access)
        _patch(self, f'{_ENGINE}.ai_workflows_db_add', _add)
        _patch(self, f'{_ENGINE}.ai_workflows_db_commit', _record('commit'))
        patch_skip_counter(self, self.workflows)

    def _workflow(self, **trigger_config):
        config = {'hooks': ['on_postload_alert_create']}
        config.update(trigger_config)
        wf = workflow(chain(), trigger_type='event', trigger_config=config, skipped_count=0, last_skip_reason=None,
                      last_skipped_at=None)
        self.workflows[wf.id] = wf
        return wf

    def _fire(self, wf):
        return [self.store.runs[i] for i in ai_workflows_triggers_process_event(_event(), [wf.id])]


class TestsAccessBeforeCondition(_TriggerCase):

    def test_refused_access_should_look_the_same_whatever_the_condition(self):
        self.can_access = False
        matching = self._fire(self._workflow(condition='{{ entity_id == 5 }}'))
        not_matching = self._fire(self._workflow(condition='{{ entity_id == 6 }}'))
        self.assertEqual([('skipped', matching[0].error)], [(r.status, r.error) for r in not_matching])
        self.assertEqual('skipped', matching[0].status)

    def test_condition_should_not_be_evaluated_when_access_is_refused(self):
        self.can_access = False
        evaluate = _patch(self, f'{_TRIGGERS}.ai_workflows_context_eval_expression', MagicMock(return_value=True))
        self._fire(self._workflow(condition='{{ true }}'))
        evaluate.assert_not_called()
        self.assertNotIn('lock', self.calls)

    def test_condition_should_apply_once_access_is_granted(self):
        self.assertEqual([], self._fire(self._workflow(condition='{{ entity_id == 6 }}')))
        self.assertEqual({}, self.store.runs)


class TestsWorkflowLock(_TriggerCase):

    def test_checks_and_insert_should_run_under_the_workflow_lock(self):
        runs = self._fire(self._workflow(dedup_minutes=10))
        self.assertEqual(['running'], [r.status for r in runs])
        calls = self.calls
        self.assertLess(calls.index('access'), calls.index('lock'))
        locked = calls[calls.index('lock'):]
        self.assertEqual(['lock', 'dedup', 'active', 'insert', 'commit'], locked)

    def test_workflow_deactivated_meanwhile_should_not_start(self):
        wf = self._workflow()
        locked = SimpleNamespace(**{**vars(wf), 'is_active': False})
        _patch(self, f'{_TRIGGERS}.ai_workflows_db_lock_workflow', lambda _id: locked)
        self.assertEqual([], self._fire(wf))
        self.assertEqual({}, self.store.runs)

    def test_suppression_should_release_the_lock_without_a_run(self):
        wf = self._workflow(dedup_minutes=10)
        self.dedup_hit = object()
        self.assertEqual([], self._fire(wf))
        self.assertNotIn('insert', self.calls)
        self.assertEqual('commit', self.calls[-1])
        self.assertEqual(1, wf.skipped_count)


class TestsSkipCounting(EngineTestCase):

    def test_skip_run_should_count_on_the_workflow_and_write_no_run(self):
        wf = workflow(chain(), skipped_count=2, last_skip_reason=None, last_skipped_at=None)
        patch_skip_counter(self, {wf.id: wf})
        self.assertIsNone(ai_workflows_engine_skip_run(wf, 'event', 'x' * 100, run_as_user_id=OWNER_ID,
                                                       entity_type='alert', entity_id=5))
        self.assertEqual((3, 'x' * 64), (wf.skipped_count, wf.last_skip_reason))
        self.assertIsNotNone(wf.last_skipped_at)
        self.assertEqual({}, self.store.runs)


class TestsWildcard(EngineTestCase):

    def test_wildcard_should_not_match_the_engine_own_hooks(self):
        config = {'hooks': ['*']}
        for hook in ('on_postload_ai_workflow_run_complete', 'on_postload_ai_suggestion_create',
                     'on_postload_ai_suggestion_update', 'on_postload_notification_create'):
            self.assertFalse(triggers._hook_matches(config, hook), hook)
        self.assertTrue(triggers._hook_matches(config, 'on_postload_alert_create'))

    def test_listed_hooks_should_still_match(self):
        config = {'hooks': ['on_postload_ai_workflow_run_complete']}
        self.assertTrue(triggers._hook_matches(config, 'on_postload_ai_workflow_run_complete'))


class TestsHookListener(EngineTestCase):

    def setUp(self):
        super().setUp()
        triggers._hook_cache.clear()
        self.addCleanup(triggers._hook_cache.clear)
        self.now = [1000.0]
        self.hooks = _patch(self, f'{_TRIGGERS}.ai_workflows_db_active_event_hooks',
                            MagicMock(return_value=[(11, ['*']), (12, ['on_postload_case_create'])]))
        self.listed = _patch(self, f'{_TRIGGERS}.ai_workflows_db_list_active', MagicMock(return_value=[]))
        self.commit = _patch(self, f'{_TRIGGERS}.ai_workflows_db_commit', MagicMock())
        self.rollback = _patch(self, f'{_TRIGGERS}.ai_workflows_db_rollback', MagicMock())
        _patch(self, f'{_TRIGGERS}.time.monotonic', lambda: self.now[0])
        _patch(self, f'{_TRIGGERS}.webhooks_build_event', lambda hook, _data, **_kwargs: {'event': hook})
        _patch(self, f'{_TRIGGERS}.webhooks_current_actor', lambda: None)
        self.delay = _patch(self, 'app.iris_engine.ai_workflows.tasks.ai_workflows_task_trigger.delay', MagicMock())

    def test_hook_should_enqueue_the_matching_workflows(self):
        triggers._on_hook('on_postload_alert_create', {})
        self.assertEqual([11], self.delay.call_args.args[1])
        self.listed.assert_not_called()

    def test_engine_own_hooks_should_not_reach_wildcard_workflows(self):
        triggers._on_hook('on_postload_ai_workflow_run_complete', {})
        self.delay.assert_not_called()

    def test_hooks_should_be_read_once_per_ttl(self):
        triggers._on_hook('on_postload_alert_create', {})
        triggers._on_hook('on_postload_case_create', {})
        self.assertEqual(1, self.hooks.call_count)
        self.now[0] += 11
        triggers._on_hook('on_postload_alert_create', {})
        self.assertEqual(2, self.hooks.call_count)

    def test_listener_should_never_end_the_caller_transaction(self):
        self.hooks.side_effect = RuntimeError('db down')
        triggers._on_hook('on_postload_alert_create', {})
        self.commit.assert_not_called()
        self.rollback.assert_not_called()
        self.delay.assert_not_called()


class TestsCronTargets(EngineTestCase):

    def setUp(self):
        super().setUp()
        self.wf = workflow(chain(), trigger_type='cron',
                           trigger_config={'cron': '*/5 * * * *', 'target': 'cases', 'max_targets': 3})
        self.filtered_for = []
        self.pages = []

        def _open_cases(limit, _scope, after_id=None):
            self.pages.append(after_id)
            start = 1000 if after_id is None else after_id
            return list(range(start - 1, max(start - 1 - limit, 0), -1))

        def _filter(user_id, _type, ids):
            self.filtered_for.append(user_id)
            return [i for i in ids if i % 97 == 0]

        _patch(self, f'{_TRIGGERS}.ai_workflows_db_list_active', lambda _type: [self.wf])
        _patch(self, f'{_TRIGGERS}.ai_workflows_db_lock_workflow', lambda _id: self.wf)
        _patch(self, f'{_TRIGGERS}.ai_workflows_db_commit', lambda: None)
        _patch(self, f'{_TRIGGERS}.ai_workflows_db_active_run_for_entity', lambda *_args: None)
        _patch(self, f'{_TRIGGERS}.ai_workflows_db_open_case_ids', _open_cases)
        _patch(self, f'{_TRIGGERS}.ai_workflows_entities_filter_accessible', _filter)

    def test_schedule_should_target_only_entities_the_owner_can_access(self):
        run_ids = ai_workflows_triggers_cron_tick(datetime.datetime(2026, 10, 5, 10, 15))
        runs = [self.store.runs[i] for i in run_ids]
        self.assertEqual([970, 873, 776], [r.entity_id for r in runs])
        self.assertEqual({OWNER_ID}, set(self.filtered_for))
        # Pages until enough accessible targets were found, then stops
        self.assertEqual([None, 900, 800], self.pages)

    def test_schedule_should_stop_after_a_bounded_number_of_pages(self):
        _patch(self, f'{_TRIGGERS}.ai_workflows_entities_filter_accessible', lambda _user, _type, _ids: [])
        self.assertEqual([], ai_workflows_triggers_cron_tick(datetime.datetime(2026, 10, 5, 10, 15)))
        self.assertEqual(triggers._MAX_TARGET_PAGES, len(self.pages))

    def test_war_rooms_should_be_filtered_by_customer_scope(self):
        self.wf.trigger_config = {'cron': '*/5 * * * *', 'target': 'war_rooms'}
        self.wf.customer_scope = [1]
        _patch(self, f'{_TRIGGERS}.ai_workflows_db_open_war_room_ids',
               lambda _limit, after_id=None: [4, 5, 6] if after_id is None else [])
        _patch(self, f'{_TRIGGERS}.ai_workflows_entities_filter_accessible', lambda _user, _type, ids: list(ids))
        _patch(self, f'{_TRIGGERS}.ai_workflows_entities_customer', lambda _type, i: 1 if i != 5 else 2)
        run_ids = ai_workflows_triggers_cron_tick(datetime.datetime(2026, 10, 5, 10, 15))
        self.assertEqual([4, 6], [self.store.runs[i].entity_id for i in run_ids])
