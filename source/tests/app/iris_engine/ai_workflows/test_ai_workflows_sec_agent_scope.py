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

"""Scope of the tool calls of an AI workflow run: identifiers pinned or
checked against the run's entity and customers, searches and listings
restricted to them, and the chatbot policy of entity-less runs. The
lookups are patched; nothing touches the database."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from app.iris_engine.ai_workflows.agent import AiWorkflowScopeError
from app.iris_engine.ai_workflows.agent import _policy
from app.iris_engine.ai_workflows.agent import ai_workflows_agent_pin_arguments

_AGENT = 'app.iris_engine.ai_workflows.agent'

# id -> customer
_CASES = {42: 1, 43: 1, 50: 2}
_ALERTS = {7: 1, 8: 2}
_CLUSTERS = {3: 1, 4: 2}
_WAR_ROOM_CASES = {9: {42, 50}}


def _run(entity_type=None, entity_id=None, customer_id=None, scope=None):
    return SimpleNamespace(uuid='r', entity_type=entity_type, entity_id=entity_id, customer_id=customer_id,
                           definition_snapshot={'customer_scope': scope or []})


def _lookup(table):
    return lambda ids: {i: table[i] for i in ids if i in table}


class _ScopeTestCase(TestCase):

    def setUp(self):
        self.customer_cases = []
        patches = {
            f'{_AGENT}.ai_workflows_runtime_db_case_customers': _lookup(_CASES),
            f'{_AGENT}.ai_workflows_runtime_db_alert_customers': _lookup(_ALERTS),
            f'{_AGENT}.ai_workflows_runtime_db_cluster_customers': _lookup(_CLUSTERS),
            f'{_AGENT}.ai_workflows_runtime_db_war_room_case_ids': lambda wr: set(_WAR_ROOM_CASES.get(int(wr), ())),
            f'{_AGENT}.ai_workflows_runtime_db_customer_case_ids': self._customer_cases,
            f'{_AGENT}.ai_workflows_db_entity_customer': lambda _t, _i: None,
        }
        for target, replacement in patches.items():
            patcher = patch(target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _customer_cases(self, customers, limit=1000):
        self.customer_cases.append(sorted(customers))
        return sorted(i for i, c in _CASES.items() if c in customers)

    def pin(self, run, tool, arguments, strict=True):
        return ai_workflows_agent_pin_arguments(run, tool, arguments, strict=strict)

    def refused(self, run, tool, arguments, strict=True):
        with self.assertRaises(AiWorkflowScopeError, msg=f'{tool} {arguments}'):
            self.pin(run, tool, arguments, strict=strict)


class TestsPinnedIdentifiers(_ScopeTestCase):

    def test_case_run_should_pin_the_case(self):
        run = _run('case', 42, customer_id=1)
        self.assertEqual({'case_identifier': 42}, self.pin(run, 'iris_cases_get', {}))
        self.assertEqual({'case_identifier': 42}, self.pin(run, 'iris_cases_get', {'case_identifier': '42'}))

    def test_model_pointing_at_another_case_should_be_refused(self):
        self.refused(_run('case', 42, customer_id=1), 'iris_cases_get', {'case_identifier': 43})

    def test_configured_arguments_should_be_overridden_instead(self):
        run = _run('case', 42, customer_id=1)
        self.assertEqual({'case_identifier': 42}, self.pin(run, 'iris_cases_get', {'case_identifier': 43},
                                                           strict=False))

    def test_alert_run_should_pin_writes_to_its_alert(self):
        run = _run('alert', 7, customer_id=1)
        self.refused(run, 'iris_alerts_update', {'alert_identifier': 8, 'payload': {}})
        self.assertEqual(7, self.pin(run, 'iris_alerts_update', {'payload': {}})['alert_identifier'])

    def test_alert_run_should_read_alerts_of_its_customer_only(self):
        run = _run('alert', 7, customer_id=1)
        self.assertEqual({'alert_identifier': 7}, self.pin(run, 'iris_alerts_get', {'alert_identifier': 7}))
        self.refused(run, 'iris_alerts_get', {'alert_identifier': 8})
        self.refused(run, 'iris_alerts_get', {'alert_identifier': 999})

    def test_merge_target_should_be_a_case_of_the_customer(self):
        run = _run('alert', 7, customer_id=1)
        self.assertEqual(43, self.pin(run, 'iris_alerts_merge', {'target_case_id': 43})['target_case_id'])
        self.refused(run, 'iris_alerts_merge', {'target_case_id': 50})

    def test_alert_list_identifiers_should_all_be_in_scope(self):
        run = _run('alert', 7, customer_id=1)
        self.refused(run, 'iris_alerts_list', {'alert_identifiers': '7, 8'})

    def test_non_integer_identifiers_should_be_refused(self):
        run = _run('alert', 7, customer_id=1)
        for value in (True, 'seven', [{'id': 7}], 7.5):
            self.refused(run, 'iris_alerts_get', {'alert_identifier': value})

    def test_war_room_run_should_reach_its_cases_only(self):
        run = _run('war_room', 9)
        self.assertEqual(50, self.pin(run, 'iris_cases_get', {'case_identifier': 50})['case_identifier'])
        self.refused(run, 'iris_cases_get', {'case_identifier': 43})
        self.refused(run, 'iris_war_rooms_get', {'war_room_id': 10})

    def test_other_war_room_should_be_refused_on_a_case_run(self):
        self.refused(_run('case', 42, customer_id=1), 'iris_war_rooms_get', {'war_room_id': 9})

    def test_lookup_failure_should_fail_closed(self):
        with patch(f'{_AGENT}.ai_workflows_runtime_db_alert_customers', side_effect=RuntimeError('db down')):
            self.refused(_run('alert', 7, customer_id=1), 'iris_alerts_get', {'alert_identifier': 8})


class TestsPinnedReads(_ScopeTestCase):

    def test_search_should_be_pinned_to_the_case(self):
        self.assertEqual(42, self.pin(_run('case', 42, customer_id=1), 'iris_search', {'value': 'x'})['case_id'])

    def test_search_should_be_pinned_to_the_war_room_cases(self):
        self.assertEqual([42, 50], self.pin(_run('war_room', 9), 'iris_search', {'value': 'x'})['case_ids'])

    def test_search_should_be_pinned_to_the_customer_cases(self):
        arguments = self.pin(_run('alert', 7, customer_id=1), 'iris_search', {'value': 'x'})
        self.assertEqual([42, 43], arguments['case_ids'])

    def test_search_in_another_customer_case_should_be_refused(self):
        self.refused(_run('alert', 7, customer_id=1), 'iris_search', {'value': 'x', 'case_ids': [42, 50]})

    def test_search_without_case_in_scope_should_be_refused(self):
        self.refused(_run(scope=[3]), 'iris_search', {'value': 'x'})

    def test_unscoped_run_should_search_freely(self):
        self.assertEqual({'value': 'x'}, self.pin(_run(), 'iris_search', {'value': 'x'}))

    def test_customer_filter_should_be_injected_with_the_schema_type(self):
        run = _run('alert', 7, customer_id=1)
        self.assertEqual(1, self.pin(run, 'iris_alerts_list', {})['customer_identifier'])
        self.assertEqual('1', self.pin(run, 'iris_cases_filter', {})['case_customer_id'])
        self.refused(run, 'iris_cases_filter', {'case_customer_id': '2'})

    def test_several_customers_should_need_an_explicit_filter(self):
        run = _run(scope=[1, 2])
        self.refused(run, 'iris_alerts_list', {})
        self.assertEqual(2, self.pin(run, 'iris_alerts_list', {'customer_identifier': 2})['customer_identifier'])
        self.refused(run, 'iris_alerts_list', {'customer_identifier': 3})

    def test_unfilterable_lists_should_be_refused_on_a_scoped_run(self):
        self.refused(_run(scope=[1]), 'iris_cases_list', {})
        self.assertEqual({}, self.pin(_run(), 'iris_cases_list', {}))


class TestsEntitylessPolicy(TestCase):

    def test_single_scope_customer_should_use_its_policy(self):
        policy = SimpleNamespace(restriction_level=2)
        with patch(f'{_AGENT}.ai_workflows_db_policy_for_customer', return_value=policy) as single, \
                patch(f'{_AGENT}.ai_workflows_runtime_db_strictest_policy') as strictest:
            self.assertEqual((policy, 2), _policy(_run(scope=[5])))
        single.assert_called_once_with(5)
        strictest.assert_not_called()

    def test_several_scope_customers_should_use_the_strictest_policy(self):
        policy = SimpleNamespace(restriction_level=3)
        with patch(f'{_AGENT}.ai_workflows_runtime_db_strictest_policy', return_value=policy) as strictest:
            self.assertEqual((policy, 3), _policy(_run(scope=[5, 6])))
        strictest.assert_called_once_with([5, 6])

    def test_unscoped_run_should_use_the_strictest_policy_of_all(self):
        with patch(f'{_AGENT}.ai_workflows_runtime_db_strictest_policy', return_value=None) as strictest:
            self.assertEqual((None, 0), _policy(_run()))
        strictest.assert_called_once_with(None)
