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

"""The detection rule feedback example: it validates, its scripts count
the resolved alerts per rule and verdict, and a run pages through the
alerts, has the AI propose tunings and tells the owner."""

import json
import os
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from sqlalchemy.dialects import postgresql

from app.datamgmt.lucene.query_compiler import compile_alert_query
from app.iris_engine.ai_workflows.engine import ai_workflows_engine_expire_wait
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
                     'examples', 'detection_rule_feedback.workflow.json')
with open(_PATH, encoding='utf-8') as _handle:
    _DOCUMENT = json.load(_handle)
_NODES = {n['id']: n for n in _DOCUMENT['workflow']['graph']['nodes']}


def _script(node_id, inputs):
    outcome = ai_workflows_sandbox_run(_NODES[node_id]['config']['code'], {'inputs': inputs},
                                       max_steps=_NODES[node_id]['config']['max_steps'])
    if not outcome['ok']:
        raise AssertionError(outcome)
    return outcome['result']


def _alert(alert_id, title, resolution, source='splunk', created='2026-10-05T10:00:00.000000',
           resolved='2026-10-05T12:00:00'):
    return {'alert_id': alert_id, 'alert_title': title, 'alert_source': source, 'alert_creation_time': created,
            'resolved_at': resolved, 'resolution_status': {'resolution_status_name': resolution}}


def _page(alerts, next_page=None, total=None):
    return {'total': total if total is not None else len(alerts), 'data': alerts, 'last_page': 1,
            'current_page': 1, 'next_page': next_page}


def _merge(page, stats=None, page_no=1, max_pages=30):
    return _script('merge', {'page': page, 'stats': stats, 'page_no': page_no, 'max_pages': max_pages})


class TestsDetectionFeedbackDocument(TestCase):

    def test_workflow_should_validate(self):
        body = ai_workflows_portable_read_workflow(_DOCUMENT)
        self.assertEqual([], ai_workflows_graph_validate_trigger_config(body['trigger_type'], body['trigger_config']))
        self.assertEqual([], ai_workflows_graph_validate(body['graph'], body['trigger_type'], body['trigger_config'],
                                                         body['write_tool_allowlist']))

    def test_workflow_should_run_weekly_without_any_write(self):
        body = ai_workflows_portable_read_workflow(_DOCUMENT)
        self.assertEqual(('cron', '0 7 * * 1', 'none', []),
                         (body['trigger_type'], body['trigger_config']['cron'], body['trigger_config']['target'],
                          body['write_tool_allowlist']))

    def test_requirements_should_match_the_graph(self):
        nodes = list(_NODES.values())
        self.assertEqual([], ai_workflows_portable_literal_secrets(nodes))
        self.assertEqual([], ai_workflows_portable_key_references(nodes))
        self.assertEqual({'keystore': [], 'tools': ai_workflows_portable_tools(nodes, [])},
                         _DOCUMENT['requirements'])


class TestsDetectionFeedbackWindowScript(TestCase):

    def test_window_should_cover_the_last_days_as_an_alert_query(self):
        result = _script('window', {'now': '2026-10-09T07:00:01.123456+00:00', 'days': 7})
        self.assertEqual({'since': '2026-10-02T07:00:01', 'until': '2026-10-09T07:00:01', 'days': 7,
                          'query': 'resolved:[2026-10-02T07:00:01 TO 2026-10-09T07:00:01]'}, result)

    def test_window_query_should_compile(self):
        query = _script('window', {'now': '2026-10-09T07:00:00+00:00', 'days': 7})['query']
        sql = str(compile_alert_query(query).compile(dialect=postgresql.dialect()))
        self.assertIn('resolved_at', sql)

    def test_unreadable_time_should_fail(self):
        outcome = ai_workflows_sandbox_run(_NODES['window']['config']['code'], {'inputs': {'now': 'soon'}})
        self.assertEqual(('failed', 'Unreadable time: soon'), (outcome['kind'], outcome['error']))


class TestsDetectionFeedbackMergeScript(TestCase):

    def test_alerts_of_one_rule_should_be_counted_together(self):
        result = _merge(_page([
            _alert(1, 'Login from 10.0.0.1 failed 5 times', 'False Positive'),
            _alert(2, 'Login from 10.0.0.2 failed 7 times', 'Legitimate'),
            _alert(3, "Hash deadbeefdeadbeefdeadbeef seen in 'C:\\temp'", 'True Positive With Impact',
                   source='edr', created=None),
        ], next_page=2, total=10))
        rules = result['stats']['rules']
        self.assertEqual(['edr | Hash {hash} seen in {value}', 'splunk | Login from {ip} failed {n} times'],
                         sorted(rules))
        login = rules['splunk | Login from {ip} failed {n} times']
        self.assertEqual((2, 1, 1, 0, 14400, 2, [1, 2]),
                         (login['alerts'], login['false_positive'], login['benign'], login['true_positive'],
                          login['close_seconds'], login['timed'], login['samples']))
        hashed = rules['edr | Hash {hash} seen in {value}']
        self.assertEqual((1, 0), (hashed['true_positive'], hashed['timed']))
        self.assertEqual((True, 2, 3, 10), (result['more'], result['next_page'], result['stats']['alerts'],
                                            result['stats']['total']))

    def test_next_page_should_add_to_the_counts(self):
        first = _merge(_page([_alert(1, 'Rule A', 'False Positive')], next_page=2))
        second = _merge(_page([_alert(2, 'Rule A', 'Not Applicable'), _alert(3, 'Rule A', 'Unknown')]),
                        stats=first['stats'], page_no=2)
        rule = second['stats']['rules']['splunk | Rule A']
        self.assertEqual((3, 1, 1, 1, [1, 2, 3]),
                         (rule['alerts'], rule['false_positive'], rule['benign'], rule['other'], rule['samples']))
        self.assertEqual((False, False, 2), (second['more'], second['stats']['truncated'], second['stats']['pages']))

    def test_page_limit_should_stop_and_flag_truncation(self):
        result = _merge(_page([_alert(1, 'Rule A', 'False Positive')], next_page=4), page_no=3, max_pages=3)
        self.assertEqual((False, True), (result['more'], result['stats']['truncated']))

    def test_clipped_page_should_be_flagged(self):
        page = _page([_alert(1, 'Rule A', 'False Positive')])
        page['_truncated'] = {'dropped': 10}
        self.assertTrue(_merge(page)['stats']['clipped'])

    def test_samples_should_be_capped(self):
        result = _merge(_page([_alert(i, 'Rule A', 'False Positive') for i in range(1, 9)]))
        self.assertEqual([1, 2, 3, 4, 5], result['stats']['rules']['splunk | Rule A']['samples'])


class TestsDetectionFeedbackReportScript(TestCase):

    def _stats(self):
        alerts = [_alert(i, 'Noisy rule', 'False Positive') for i in range(1, 7)]
        alerts += [_alert(7, 'Noisy rule', 'True Positive Without Impact')]
        alerts += [_alert(i, 'Good rule', 'True Positive With Impact', source='edr') for i in range(10, 20)]
        alerts += [_alert(i, 'Rare rule', 'False Positive') for i in range(20, 22)]
        return _merge(_page(alerts))['stats']

    def _report(self, stats, **overrides):
        inputs = {'stats': stats, 'min_alerts': 5, 'noise_threshold': 60, 'max_candidates': 15}
        inputs.update(overrides)
        return _script('report', inputs)

    def test_noisy_rules_should_be_candidates(self):
        result = self._report(self._stats())
        [candidate] = result['candidates']
        self.assertEqual(('Noisy rule', 7, 6, 86, 86, 2.0, [1, 2, 3, 4, 5]),
                         (candidate['rule'], candidate['alerts'], candidate['false_positive'],
                          candidate['false_positive_rate'], candidate['noise_rate'],
                          candidate['mean_hours_to_close'], candidate['sample_alert_ids']))
        self.assertEqual((19, 3, 8, 42, True, ''),
                         (result['alerts'], result['rules'], result['noise_alerts'], result['noise_rate'],
                          result['has_candidates'], result['coverage']))
        self.assertIn('| Noisy rule | splunk | 7 | 6 (86%) | 0 | 1 | 2.0 h |', result['table'])

    def test_thresholds_should_be_configurable(self):
        result = self._report(self._stats(), min_alerts=2, max_candidates=1)
        self.assertEqual(['Noisy rule'], [c['rule'] for c in result['candidates']])
        result = self._report(self._stats(), min_alerts=2)
        self.assertEqual(['Noisy rule', 'Rare rule'], [c['rule'] for c in result['candidates']])

    def test_quiet_week_should_have_no_candidate(self):
        result = self._report(_merge(_page([]))['stats'])
        self.assertEqual((False, 0, ''), (result['has_candidates'], result['alerts'], result['table']))

    def test_partial_review_should_be_said(self):
        stats = self._stats()
        stats['truncated'] = True
        stats['total'] = 5000
        self.assertIn('first 19 of 5000', self._report(stats)['coverage'])


_PROPOSAL = {'summary': 'One rule fires on a scheduled task.', 'proposals': [
    {'rule': 'Noisy rule', 'action': 'exclude', 'change': 'Exclude parent process backup.exe',
     'rationale': 'All 5 samples come from backup.exe', 'expected_reduction': '85%',
     'keeps_true_positives': True}]}


class _Pages(dict):
    """`tool_results` answering iris_alerts_list from a list of pages, in
    order, and keeping the arguments of every call."""

    def __init__(self, pages):
        super().__init__()
        self.pages = list(pages)
        self.calls = 0

    def get(self, tool_name, default=None):
        if tool_name != 'iris_alerts_list':
            return super().get(tool_name, default)
        page = self.pages[min(self.calls, len(self.pages) - 1)]
        self.calls += 1
        return page


class TestsDetectionFeedbackRun(EngineTestCase):

    def setUp(self):
        super().setUp()
        self.notified = []
        patches = {
            'app.iris_engine.ai_workflows.nodes.ai_workflows_sandbox_execute':
                lambda source, variables, max_steps=None, timeout_seconds=None:
                    ai_workflows_sandbox_run(source, variables, max_steps=max_steps, timeout_seconds=timeout_seconds),
            # A run about no entity notifies the active users of its audience
            'app.iris_engine.ai_workflows.nodes.ai_workflows_db_get_user':
                lambda user_id: SimpleNamespace(id=user_id, active=True),
            'app.iris_engine.notifications.service.notify_many':
                lambda users, *args, **kwargs: self.notified.append((list(users), args, kwargs)),
        }
        for target, replacement in patches.items():
            patcher = patch(target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _run(self, pages):
        self.tool_results = _Pages(pages)
        body = ai_workflows_portable_read_workflow(_DOCUMENT)
        wf = workflow(body['graph'], trigger_type='cron', trigger_config=body['trigger_config'],
                      suggestion_audience='owner')
        run = ai_workflows_engine_start_run(wf, 'cron', run_as_user_id=OWNER_ID)
        for _ in range(60):
            if run.id in self.enqueued:
                self.enqueued.remove(run.id)
                ai_workflows_engine_step(run.id)
            elif run.status == 'waiting':
                [wait] = self.store.pending_waits(run.id)
                ai_workflows_engine_expire_wait(wait)
            else:
                break
        return run

    def _lists(self):
        return [arguments for tool, arguments, _mode in self.executed if tool == 'iris_alerts_list']

    def test_noisy_rule_should_get_a_tuning_proposal(self):
        provider = self.use_provider(tool_turn('iris_alerts_get', {'alert_id': 1}),
                                     tool_turn('set_output', _PROPOSAL, 'tu2'), text_turn('done'))
        alerts = [_alert(i, 'Noisy rule', 'False Positive') for i in range(1, 7)]
        run = self._run([_page(alerts[:3], next_page=2, total=6), _page(alerts[3:], total=6)])
        self.assertEqual('succeeded', run.status, run.error)
        first, second = self._lists()
        self.assertEqual((1, 2, 50), (first['page'], second['page'], first['per_page']))
        self.assertTrue(first['query'].startswith('resolved:['))
        self.assertIn('resolved_at', first['fields'])
        self.assertIn('Noisy rule', json.dumps(provider.calls[0]['messages'], default=str))
        [suggestion] = self.store.suggestions
        self.assertIn('Exclude parent process backup.exe', suggestion.body)
        self.assertIn('| Noisy rule | splunk | 6 | 6 (100%)', suggestion.body)
        self.assertEqual('Detection tuning: 1 noisy rule(s) to review', suggestion.title)
        [(users, args, kwargs)] = self.notified
        self.assertEqual([OWNER_ID], users)
        self.assertIn('One rule fires on a scheduled task.', kwargs['body'])

    def test_quiet_week_should_stop_without_the_ai(self):
        provider = self.use_provider()
        run = self._run([_page([_alert(1, 'Good rule', 'True Positive With Impact')])])
        self.assertEqual('succeeded', run.status, run.error)
        self.assertEqual('quiet', self.steps(run)[-1].node_id)
        self.assertEqual([], provider.calls)
        self.assertEqual(([], []), (self.store.suggestions, self.notified))

    def test_agent_failure_should_still_send_the_numbers(self):
        self.use_provider(text_turn('no idea'), text_turn('still no idea'))
        alerts = [_alert(i, 'Noisy rule', 'False Positive') for i in range(1, 7)]
        run = self._run([_page(alerts)])
        self.assertEqual('notify_raw', self.steps(run)[-1].node_id)
        [(_users, _args, kwargs)] = self.notified
        self.assertIn('| Noisy rule | splunk | 6 |', kwargs['body'])
        self.assertEqual([], self.store.suggestions)
