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

"""The alert SLA watchdog library workflow: it validates, its script finds
the open unassigned alerts waiting past the time allowed for their
severity, and a run tells the owner only about new breaches."""

import datetime
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
from tests.app.iris_engine.ai_workflows.harness import workflow

_PATH = os.path.join(os.path.dirname(__file__), '..', '..', '..', '..', 'app', 'iris_engine', 'ai_workflows',
                     'examples', 'alert_sla_watchdog.workflow.json')
with open(_PATH, encoding='utf-8') as _handle:
    _DOCUMENT = json.load(_handle)
_NODES = {n['id']: n for n in _DOCUMENT['workflow']['graph']['nodes']}
_SLA = {'Critical': 15, 'High': 60, 'Medium': 240, 'Low': 1440, 'Informational': 2880, 'Unspecified': 1440}
_NOW = datetime.datetime(2026, 3, 1, 12, 0, tzinfo=datetime.timezone.utc)


def _ago(minutes, now=_NOW):
    return (now - datetime.timedelta(minutes=minutes)).strftime('%Y-%m-%dT%H:%M:%S.%f')


def _alert(alert_id, severity, minutes, now=_NOW, title=None):
    return {'alert_id': alert_id, 'alert_title': title or f'Alert {alert_id}', 'alert_creation_time': _ago(minutes, now),
            'severity': {'severity_name': severity}, 'customer': {'customer_name': 'ACME'}}


def _check(alerts, total=None, now=_NOW, max_rows=15):
    page = {'total': len(alerts) if total is None else total, 'data': alerts}
    outcome = ai_workflows_sandbox_run(_NODES['check']['config']['code'], {'inputs': {
        'page': page, 'now': now.isoformat(), 'sla_minutes': _SLA, 'default_minutes': 1440, 'interval_minutes': 15,
        'max_rows': max_rows}}, max_steps=_NODES['check']['config']['max_steps'])
    if not outcome['ok']:
        raise AssertionError(outcome)
    return outcome['result']


class TestsAlertSlaDocument(TestCase):

    def test_workflow_should_validate(self):
        body = ai_workflows_portable_read_workflow(_DOCUMENT)
        self.assertEqual([], ai_workflows_graph_validate_trigger_config(body['trigger_type'], body['trigger_config']))
        self.assertEqual([], ai_workflows_graph_validate(body['graph'], body['trigger_type'], body['trigger_config'],
                                                         body['write_tool_allowlist']))

    def test_workflow_should_run_every_quarter_hour_without_ai(self):
        body = ai_workflows_portable_read_workflow(_DOCUMENT)
        self.assertEqual(('cron', '*/15 * * * *', []), (body['trigger_type'], body['trigger_config']['cron'],
                                                        body['write_tool_allowlist']))
        self.assertNotIn('ai_agent', [n['type'] for n in _NODES.values()])

    def test_requirements_should_match_the_graph(self):
        nodes = list(_NODES.values())
        self.assertEqual([], ai_workflows_portable_literal_secrets(nodes))
        self.assertEqual([], ai_workflows_portable_key_references(nodes))
        self.assertEqual({'keystore': [], 'tools': ai_workflows_portable_tools(nodes, [])},
                         _DOCUMENT['requirements'])


class TestsAlertSlaCheckScript(TestCase):

    def test_alerts_within_their_sla_should_not_breach(self):
        result = _check([_alert(1, 'Critical', 10), _alert(2, 'Low', 600)])
        self.assertEqual((0, 0, '', 2), (result['breaches'], result['new_breaches'], result['table'],
                                          result['checked']))

    def test_breaches_should_be_new_only_during_one_interval(self):
        result = _check([_alert(1, 'High', 70), _alert(2, 'High', 200)])
        self.assertEqual((2, 1), (result['breaches'], result['new_breaches']))
        self.assertEqual([(1, True, 70, 60), (2, False, 200, 60)],
                         [(r['id'], r['new'], r['waiting_minutes'], r['sla_minutes']) for r in result['rows']])

    def test_new_breaches_then_severity_should_come_first(self):
        result = _check([_alert(1, 'Low', 3000), _alert(2, 'Medium', 245), _alert(3, 'Critical', 20),
                         _alert(4, 'Critical', 400)])
        self.assertEqual([3, 2, 4, 1], [r['id'] for r in result['rows']])
        self.assertIn('| #3 Alert 3 **new** | Critical | 20 min | 15 min | ACME |', result['table'])
        self.assertIn('| #4 Alert 4 | Critical | 6 h 40 min | 15 min | ACME |', result['table'])
        self.assertIn('| #1 Alert 1 | Low | 2 d 2 h | 24 h | ACME |', result['table'])

    def test_unknown_severity_should_use_the_default(self):
        result = _check([_alert(1, 'Weird', 1450), _alert(2, None, 100)])
        self.assertEqual([(1, 'Weird', 1440)], [(r['id'], r['severity'], r['sla_minutes']) for r in result['rows']])

    def test_titles_should_not_break_the_table(self):
        result = _check([_alert(1, 'High', 70, title='a | b\nc' + 'x' * 100)])
        line = result['table'].splitlines()[2]
        self.assertEqual(6, line.count('|'))
        self.assertIn('#1 a / b c', line)
        self.assertIn('…', line)

    def test_rows_should_be_capped_and_coverage_told(self):
        result = _check([_alert(i, 'High', 70) for i in range(1, 21)], total=250, max_rows=5)
        self.assertEqual((20, 5), (result['breaches'], len(result['rows'])))
        self.assertIn('15 more not shown.', result['table'])
        self.assertEqual(' Only the newest 20 of 250 waiting alerts were checked.', result['coverage'])

    def test_unreadable_time_should_fail(self):
        outcome = ai_workflows_sandbox_run(_NODES['check']['config']['code'], {'inputs': {'page': {}, 'now': 'soon'}})
        self.assertEqual(('failed', 'Unreadable time: soon'), (outcome['kind'], outcome['error']))


class TestsAlertSlaRun(EngineTestCase):

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

    def _run(self, alerts):
        self.tool_results['iris_alerts_list'] = {'total': len(alerts), 'data': alerts}
        body = ai_workflows_portable_read_workflow(_DOCUMENT)
        wf = workflow(body['graph'], trigger_type='cron', trigger_config=body['trigger_config'],
                      suggestion_audience='owner')
        run = ai_workflows_engine_start_run(wf, 'cron', run_as_user_id=OWNER_ID)
        for _ in range(20):
            if run.id not in self.enqueued:
                break
            self.enqueued.remove(run.id)
            ai_workflows_engine_step(run.id)
        return run

    def test_new_breach_should_notify_the_owner(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        run = self._run([_alert(1, 'Critical', 20, now), _alert(2, 'High', 300, now)])
        self.assertEqual('succeeded', run.status, run.error)
        [(fetch, arguments, _mode)] = self.executed
        self.assertEqual(('iris_alerts_list', 'is:open is:unassigned'), (fetch, arguments['query']))
        [(users, args, kwargs)] = self.notified
        self.assertEqual([OWNER_ID], users)
        self.assertEqual('Alert SLA: 1 new breach(es), 2 alert(s) waiting past their SLA', args[1])
        self.assertIn('| #1 Alert 1 **new** | Critical |', kwargs['body'])
        self.assertEqual([], self.store.suggestions)

    def test_old_breaches_only_should_stay_quiet(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        run = self._run([_alert(2, 'High', 300, now)])
        self.assertEqual('succeeded', run.status, run.error)
        self.assertEqual('quiet', self.steps(run)[-1].node_id)
        self.assertEqual([], self.notified)
