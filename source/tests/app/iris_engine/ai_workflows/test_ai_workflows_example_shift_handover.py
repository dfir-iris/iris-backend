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

"""The shift handover library workflow: it validates, its script sums up
the new alerts, the backlog and the open cases, and a run hands the AI
handover to the owner, or the numbers alone when the AI fails."""

import json
import os
from types import SimpleNamespace
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
                     'examples', 'shift_handover.workflow.json')
with open(_PATH, encoding='utf-8') as _handle:
    _DOCUMENT = json.load(_handle)
_NODES = {n['id']: n for n in _DOCUMENT['workflow']['graph']['nodes']}


def _alert(alert_id, severity, owner=None, status='New'):
    return {'alert_id': alert_id, 'alert_title': f'Alert {alert_id}', 'severity': {'severity_name': severity},
            'status': {'status_name': status}, 'owner': {'user_name': owner} if owner else None}


def _case(case_id, severity, owner='analyst'):
    return {'case_id': case_id, 'name': f'#{case_id} - Case {case_id}', 'severity': {'severity_name': severity},
            'owner': {'user_name': owner}, 'state': {'state_name': 'Open'}}


def _page(rows, total=None):
    return {'total': len(rows) if total is None else total, 'data': rows}


_NEW = _page([_alert(5, 'Low'), _alert(6, 'Critical', 'alice')])
_BACKLOG = _page([_alert(1, 'High'), _alert(2, 'Medium', 'bob'), _alert(5, 'Low'), _alert(6, 'Critical', 'alice')],
                 total=140)
_CASES = _page([_case(3, 'Medium'), _case(4, 'Critical')])


def _summary(new=_NEW, backlog=_BACKLOG, cases=_CASES):
    outcome = ai_workflows_sandbox_run(_NODES['summary']['config']['code'], {'inputs': {
        'new_alerts': new, 'backlog': backlog, 'cases': cases, 'hours': 12, 'max_rows': 10}},
        max_steps=_NODES['summary']['config']['max_steps'])
    if not outcome['ok']:
        raise AssertionError(outcome)
    return outcome['result']


class TestsShiftHandoverDocument(TestCase):

    def test_workflow_should_validate(self):
        body = ai_workflows_portable_read_workflow(_DOCUMENT)
        self.assertEqual([], ai_workflows_graph_validate_trigger_config(body['trigger_type'], body['trigger_config']))
        self.assertEqual([], ai_workflows_graph_validate(body['graph'], body['trigger_type'], body['trigger_config'],
                                                         body['write_tool_allowlist']))

    def test_workflow_should_run_at_each_shift_change(self):
        body = ai_workflows_portable_read_workflow(_DOCUMENT)
        self.assertEqual(('cron', '0 7,19 * * *', []), (body['trigger_type'], body['trigger_config']['cron'],
                                                        body['write_tool_allowlist']))

    def test_requirements_should_match_the_graph(self):
        nodes = list(_NODES.values())
        self.assertEqual([], ai_workflows_portable_literal_secrets(nodes))
        self.assertEqual([], ai_workflows_portable_key_references(nodes))
        self.assertEqual({'keystore': [], 'tools': ai_workflows_portable_tools(nodes, [])},
                         _DOCUMENT['requirements'])


class TestsShiftHandoverSummaryScript(TestCase):

    def test_numbers_should_count_by_severity(self):
        result = _summary()
        self.assertEqual('Last 12 h: 2 new alert(s) (1 Critical, 1 Low). Open alerts: 140, 2 of the 4 newest '
                         'unassigned (1 Critical, 1 High, 1 Medium, 1 Low). Open cases: 2.', result['numbers'])
        self.assertEqual((2, 140, 2, 2, False), (result['new_alerts'], result['open_alerts'], result['unassigned'],
                                                 result['open_cases'], result['quiet']))

    def test_tables_should_put_the_most_severe_first(self):
        report = _summary()['report']
        self.assertIn('#### New alerts\n| Alert | Severity | Status | Owner |\n|---|---|---|---|\n'
                      '| #6 Alert 6 | Critical | New | alice |\n| #5 Alert 5 | Low | New | unassigned |', report)
        self.assertIn('| #4 #4 - Case 4 | Critical | analyst | Open |\n| #3 #3 - Case 3 | Medium |', report)

    def test_nothing_should_be_quiet(self):
        result = _summary(new=_page([]), backlog=_page([]), cases=_page([]))
        self.assertTrue(result['quiet'])
        self.assertEqual('Last 12 h: 0 new alert(s) (none). Open alerts: 0, 0 of the 0 newest unassigned (none). '
                         'Open cases: 0.', result['report'])

    def test_unreadable_pages_should_count_as_empty(self):
        self.assertEqual(0, _summary(new=None, backlog='x', cases={'data': [1, None]})['open_cases'])


class _Lists(dict):
    """`tool_results` answering iris_alerts_list with the new alerts first,
    then the backlog."""

    def __init__(self, **results):
        super().__init__(**results)
        self.lists = [_NEW, _BACKLOG]

    def get(self, tool_name, default=None):
        if tool_name == 'iris_alerts_list':
            return self.lists.pop(0)
        return super().get(tool_name, default)


class TestsShiftHandoverRun(EngineTestCase):

    def setUp(self):
        super().setUp()
        self.notified = []
        patches = {
            'app.iris_engine.ai_workflows.nodes.ai_workflows_sandbox_execute':
                lambda source, variables, max_steps=None, timeout_seconds=None:
                    ai_workflows_sandbox_run(source, variables, max_steps=max_steps, timeout_seconds=timeout_seconds),
            'app.iris_engine.ai_workflows.nodes.ai_workflows_db_get_user':
                lambda user_id: SimpleNamespace(id=user_id, active=True),
            'app.iris_engine.notifications.service.notify_many':
                lambda users, *args, **kwargs: self.notified.append((list(users), args, kwargs)),
        }
        for target, replacement in patches.items():
            patcher = patch(target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _run(self):
        self.tool_results = _Lists(iris_cases_filter=_CASES)
        body = ai_workflows_portable_read_workflow(_DOCUMENT)
        wf = workflow(body['graph'], trigger_type='cron', trigger_config=body['trigger_config'],
                      suggestion_audience='owner')
        run = ai_workflows_engine_start_run(wf, 'cron', run_as_user_id=OWNER_ID)
        for _ in range(30):
            if run.id not in self.enqueued:
                break
            self.enqueued.remove(run.id)
            ai_workflows_engine_step(run.id)
        return run

    def test_handover_should_be_suggested_and_notified(self):
        provider = self.use_provider(tool_turn('set_output', {
            'headline': 'Critical alert #6 is still open', 'summary': 'A quiet night but for #6.',
            'priorities': ['Close #6', 'Assign #1'], 'watch': ['Backlog at 140']}), text_turn('done'))
        run = self._run()
        self.assertEqual('succeeded', run.status, run.error)
        self.assertEqual(['created:>now-12h', 'is:open'],
                         [arguments['query'] for tool, arguments, _mode in self.executed
                          if tool == 'iris_alerts_list'])
        self.assertIn('Open alerts: 140', json.dumps(provider.calls[0]['messages'], default=str))
        [suggestion] = self.store.suggestions
        self.assertEqual('Shift handover: Critical alert #6 is still open', suggestion.title)
        self.assertIn('#### Priorities\n- Close #6\n- Assign #1\n', suggestion.body)
        self.assertIn('#### Watch\n- Backlog at 140\n', suggestion.body)
        self.assertIn('| #4 #4 - Case 4 | Critical |', suggestion.body)
        [(users, args, kwargs)] = self.notified
        self.assertEqual(([OWNER_ID], 'Shift handover: Critical alert #6 is still open'), (users, args[1]))
        self.assertIn('- Close #6', kwargs['body'])

    def test_agent_failure_should_still_send_the_numbers(self):
        self.use_provider(text_turn('no idea'), text_turn('still no idea'))
        run = self._run()
        self.assertEqual('notify_raw', self.steps(run)[-1].node_id)
        [(_users, args, kwargs)] = self.notified
        self.assertEqual('Shift handover (numbers only)', args[1])
        self.assertIn('Open alerts: 140', kwargs['body'])
        self.assertEqual([], self.store.suggestions)
