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

"""Workflow graph and trigger config validation."""

from unittest import TestCase
from unittest.mock import patch

from app.iris_engine.ai_workflows.graph import ai_workflows_graph_targets
from app.iris_engine.ai_workflows.graph import ai_workflows_graph_validate
from app.iris_engine.ai_workflows.graph import ai_workflows_graph_validate_trigger_config

_WRITE_TOOL = 'iris_case_notes_create'
_READ_TOOL = 'iris_cases_get'


def _node(node_id, node_type, **config):
    return {'id': node_id, 'type': node_type, 'label': node_id, 'position': {'x': 0, 'y': 0}, 'config': config}


def _edge(source, target, port='out'):
    return {'id': f'{source}-{target}-{port}', 'source': source, 'target': target, 'source_port': port}


def _graph(nodes, edges):
    return {'nodes': [_node('trigger', 'trigger')] + nodes, 'edges': edges}


def _messages(errors):
    return [(e['node_id'], e['message']) for e in errors]


class TestsGraphStructure(TestCase):

    def _validate(self, graph, allowlist=None, trigger_type='manual', trigger_config=None):
        return ai_workflows_graph_validate(graph, trigger_type, trigger_config or {}, allowlist or [])

    def test_valid_graph_should_have_no_error(self):
        graph = _graph([_node('agent', 'ai_agent', prompt='Triage {{ entity.title }}', tools=[_READ_TOOL]),
                        _node('check', 'condition', rules=[{'path': 'nodes.agent.output.verdict',
                                                            'operator': 'eq', 'value': 'malicious'}]),
                        _node('note', 'action', tool=_WRITE_TOOL, arguments={'note_title': '{{ run.uuid }}'}),
                        _node('end', 'stop')],
                       [_edge('trigger', 'agent'), _edge('agent', 'check'), _edge('check', 'note', 'true'),
                        _edge('check', 'end', 'false')])
        self.assertEqual([], self._validate(graph, allowlist=[_WRITE_TOOL]))

    def test_graph_should_need_exactly_one_trigger(self):
        graph = {'nodes': [_node('a', 'trigger'), _node('b', 'trigger')], 'edges': []}
        self.assertIn((None, 'A workflow needs exactly one trigger node'), _messages(self._validate(graph)))
        graph = {'nodes': [_node('a', 'stop')], 'edges': []}
        self.assertIn((None, 'A workflow needs exactly one trigger node'), _messages(self._validate(graph)))

    def test_unreachable_node_should_be_reported(self):
        graph = _graph([_node('orphan', 'stop')], [])
        self.assertEqual([('orphan', 'Not reachable from the trigger')], _messages(self._validate(graph)))

    def test_unknown_type_and_duplicate_id_should_be_reported(self):
        graph = _graph([_node('x', 'teleport'), _node('trigger', 'stop')], [])
        messages = [m for _n, m in _messages(self._validate(graph))]
        self.assertIn('Unknown node type teleport', messages)
        self.assertIn('Duplicate node id trigger', messages)

    def test_edge_from_a_port_the_node_has_not_should_be_reported(self):
        graph = _graph([_node('end', 'stop')], [_edge('trigger', 'end', 'true')])
        self.assertIn(('trigger', 'trigger nodes have no "true" output'), _messages(self._validate(graph)))

    def test_source_handle_should_be_accepted_as_the_port(self):
        graph = _graph([_node('check', 'condition'), _node('end', 'stop')],
                       [_edge('trigger', 'check'),
                        {'id': 'e', 'source': 'check', 'target': 'end', 'sourceHandle': 'false'}])
        self.assertEqual([], self._validate(graph))
        self.assertEqual(['end'], ai_workflows_graph_targets(graph, 'check', 'false'))
        self.assertEqual([], ai_workflows_graph_targets(graph, 'check', 'true'))

    def test_edge_into_the_trigger_should_be_reported(self):
        graph = _graph([_node('vars', 'set_variables')], [_edge('trigger', 'vars'), _edge('vars', 'trigger')])
        self.assertIn(('trigger', 'Nothing can lead back to the trigger'), _messages(self._validate(graph)))

    def test_cycle_without_waiting_node_should_be_rejected(self):
        graph = _graph([_node('a', 'set_variables'), _node('b', 'set_variables')],
                       [_edge('trigger', 'a'), _edge('a', 'b'), _edge('b', 'a')])
        flagged = sorted(n for n, m in _messages(self._validate(graph)) if m.startswith('On a loop'))
        self.assertEqual(['a', 'b'], flagged)

    def test_cycle_through_a_waiting_node_should_be_allowed(self):
        for waiting in (_node('w', 'delay', minutes=5), _node('w', 'ask_analyst', question='?', fields=['x']),
                        _node('w', 'http_request', url='https://example.org', mode='async')):
            port = 'answered' if waiting['type'] == 'ask_analyst' else 'out'
            graph = _graph([_node('a', 'set_variables'), waiting],
                           [_edge('trigger', 'a'), _edge('a', 'w'), _edge('w', 'a', port)])
            self.assertEqual([], self._validate(graph), waiting['type'])

    def test_cycle_through_sync_http_or_zero_delay_should_be_rejected(self):
        for busy in (_node('w', 'http_request', url='https://example.org'), _node('w', 'delay', minutes=0)):
            graph = _graph([_node('a', 'set_variables'), busy],
                           [_edge('trigger', 'a'), _edge('a', 'w'), _edge('w', 'a')])
            self.assertTrue(any(m.startswith('On a loop') for _n, m in _messages(self._validate(graph))),
                            busy['config'])

    def test_node_behind_a_cycle_should_not_be_flagged(self):
        graph = _graph([_node('a', 'set_variables'), _node('b', 'set_variables'), _node('c', 'stop')],
                       [_edge('trigger', 'a'), _edge('a', 'b'), _edge('b', 'a'), _edge('b', 'c')])
        flagged = sorted(n for n, m in _messages(self._validate(graph)) if m.startswith('On a loop'))
        self.assertEqual(['a', 'b'], flagged)


class TestsGraphNodeConfig(TestCase):

    def _errors(self, node, allowlist=None, trigger_type='manual', trigger_config=None):
        graph = _graph([node], [_edge('trigger', node['id'])])
        return [(e['field'], e['message']) for e in
                ai_workflows_graph_validate(graph, trigger_type, trigger_config or {}, allowlist or [])]

    def test_action_with_a_write_tool_should_need_the_allowlist(self):
        node = _node('note', 'action', tool=_WRITE_TOOL)
        self.assertEqual('tool', self._errors(node)[0][0])
        self.assertEqual([], self._errors(node, allowlist=[_WRITE_TOOL]))

    def test_suggest_may_propose_a_write_tool_not_allowlisted(self):
        node = _node('s', 'suggest', title='Add a note', proposed_action={'tool': _WRITE_TOOL, 'arguments': {}})
        self.assertEqual([], self._errors(node))

    def test_unknown_tool_should_be_reported(self):
        self.assertEqual([('tools.0', 'Unknown tool nope')],
                         self._errors(_node('ai', 'ai_agent', prompt='x', tools=['nope'])))

    def test_allowlist_should_hold_write_tools_only(self):
        graph = _graph([], [])
        errors = ai_workflows_graph_validate(graph, 'manual', {}, [_READ_TOOL])
        self.assertEqual([('write_tool_allowlist', f'{_READ_TOOL} is not a write tool')],
                         [(e['field'], e['message']) for e in errors])

    def test_bad_template_should_be_reported(self):
        errors = self._errors(_node('vars', 'set_variables', variables=[{'name': 'v', 'value': '{{ oops'}]))
        self.assertEqual('variables.0.value', errors[0][0])

    def test_condition_rule_operator_should_be_known(self):
        errors = self._errors(_node('c', 'condition', rules=[{'path': 'vars.x', 'operator': 'approx'}]))
        self.assertEqual('rules.0.operator', errors[0][0])

    def test_ask_analyst_should_need_fields(self):
        errors = self._errors(_node('ask', 'ask_analyst', question='Is it real?'))
        self.assertEqual([('fields', 'Add at least one field, or a context path to read them from')], errors)
        self.assertEqual([], self._errors(_node('ask', 'ask_analyst', question='?', fields_from='vars.form')))

    def test_http_request_should_check_method_and_url(self):
        errors = dict(self._errors(_node('h', 'http_request', method='TRACE')))
        self.assertIn('method', errors)
        self.assertEqual('Required', errors['url'])

    def test_cron_without_target_should_flag_entity_nodes(self):
        node = _node('related', 'find_related')
        errors = self._errors(node, trigger_type='cron', trigger_config={'cron': '* * * * *'})
        self.assertEqual([(None, 'Needs an entity, but this schedule has no target')], errors)
        self.assertEqual([], self._errors(node, trigger_type='cron',
                                          trigger_config={'cron': '* * * * *', 'target': 'cases'}))


class TestsTriggerConfig(TestCase):

    def test_event_hooks_should_be_known_postload_hooks(self):
        hooks = [('on_postload_case_create', ''), ('on_postload_alert_create', '')]
        with patch('app.iris_engine.ai_workflows.graph.ai_workflows_db_postload_hooks', return_value=hooks):
            self.assertEqual([], ai_workflows_graph_validate_trigger_config(
                'event', {'hooks': ['on_postload_case_create'], 'dedup_minutes': 10}))
            self.assertEqual([], ai_workflows_graph_validate_trigger_config('event', {'hooks': ['*']}))
            errors = ai_workflows_graph_validate_trigger_config(
                'event', {'hooks': ['on_preload_case_create', 'on_postload_nope']})
        self.assertEqual(['on_preload_case_create is not a postload event', 'Unknown event on_postload_nope'],
                         [e['message'] for e in errors])

    def test_event_should_need_a_hook_and_a_sane_dedup(self):
        errors = ai_workflows_graph_validate_trigger_config('event', {'hooks': [], 'dedup_minutes': -1})
        self.assertEqual(['trigger_config.hooks', 'trigger_config.dedup_minutes'], [e['field'] for e in errors])

    def test_cron_should_be_parsed(self):
        self.assertEqual([], ai_workflows_graph_validate_trigger_config(
            'cron', {'cron': '*/15 8-18 * * mon-fri', 'target': 'war_rooms', 'max_targets': 20}))
        errors = ai_workflows_graph_validate_trigger_config('cron', {'cron': '61 * * * *', 'target': 'moon'})
        self.assertEqual(['trigger_config.cron', 'trigger_config.target'], [e['field'] for e in errors])

    def test_manual_and_webhook_configs(self):
        self.assertEqual([], ai_workflows_graph_validate_trigger_config('manual', {'entity_types': ['case']}))
        self.assertEqual(1, len(ai_workflows_graph_validate_trigger_config('manual', {'entity_types': ['x']})))
        errors = ai_workflows_graph_validate_trigger_config('webhook', {'entity_id_path': 'data.id'})
        self.assertEqual(['trigger_config.entity_type'], [e['field'] for e in errors])

    def test_unknown_trigger_type(self):
        self.assertEqual(['trigger_type'],
                         [e['field'] for e in ai_workflows_graph_validate_trigger_config('telepathy', {})])


class TestsGraphLimits(TestCase):

    def _messages(self, graph):
        return [(e['node_id'], e['field'], e['message']) for e in ai_workflows_graph_validate(graph, 'manual', {}, [])]

    def test_too_many_edges_should_be_rejected(self):
        graph = _graph([_node('end', 'stop')], [_edge('trigger', 'end')] * 1001)
        self.assertEqual([(None, 'graph', 'A workflow has at most 1000 edges')], self._messages(graph))

    def test_oversized_definition_should_be_rejected(self):
        graph = _graph([_node('end', 'stop')], [_edge('trigger', 'end')])
        graph['viewport'] = {'padding': 'x' * (1024 * 1024)}
        self.assertEqual([(None, 'graph', 'The workflow definition is over 1024 KB')], self._messages(graph))

    def test_oversized_config_string_should_be_rejected(self):
        node = _node('vars', 'set_variables', variables=[{'name': 'v', 'value': 'x' * (64 * 1024 + 1)}])
        messages = self._messages(_graph([node], [_edge('trigger', 'vars')]))
        self.assertEqual([('vars', 'variables.0.value', 'At most 65536 characters')], messages)

    def test_prompt_should_have_a_tighter_cap(self):
        node = _node('ai', 'ai_agent', prompt='x' * (32 * 1024 + 1))
        messages = self._messages(_graph([node], [_edge('trigger', 'ai')]))
        self.assertEqual([('ai', 'prompt', 'At most 32768 characters')], messages)
        node = _node('ai', 'ai_agent', prompt='x' * (32 * 1024))
        self.assertEqual([], self._messages(_graph([node], [_edge('trigger', 'ai')])))

    def test_long_label_should_be_rejected(self):
        node = _node('end', 'stop')
        node['label'] = 'l' * 256
        messages = self._messages(_graph([node], [_edge('trigger', 'end')]))
        self.assertEqual([('end', 'label', 'A label is a string of at most 255 characters')], messages)


class TestsGraphSecurityChecks(TestCase):

    def _errors(self, node):
        graph = _graph([node], [_edge('trigger', node['id'])])
        return [(e['field'], e['message']) for e in ai_workflows_graph_validate(graph, 'manual', {}, [])]

    def test_http_url_should_have_a_literal_scheme_and_host(self):
        for url in ('{{ vars.url }}', 'https://{{ vars.host }}/x', 'http{{ vars.s }}://example.org/',
                    'https://example.org{{ vars.port }}/x', 'ftp://example.org/', 'example.org/x',
                    'https://%65vil.example/'):
            errors = self._errors(_node('h', 'http_request', url=url))
            self.assertEqual(['url'], [field for field, _m in errors], url)

    def test_http_url_with_a_templated_path_should_be_accepted(self):
        for url in ('https://api.example.org/v1/{{ entity.id }}?q={{ vars.q }}', 'http://example.org:8080/'):
            self.assertEqual([], self._errors(_node('h', 'http_request', url=url)), url)

    def test_suggest_should_only_propose_a_write_tool(self):
        node = _node('s', 'suggest', title='Read', proposed_action={'tool': _READ_TOOL, 'arguments': {}})
        self.assertEqual([('proposed_action.tool',
                           f'{_READ_TOOL} is not a write tool: a proposed action must change something')],
                         self._errors(node))


class TestsTriggerHooks(TestCase):

    _HOOKS = [('on_postload_case_create', ''), ('on_postload_alert_create', '')]

    def _errors(self, hooks, known=_HOOKS):
        with patch('app.iris_engine.ai_workflows.graph.ai_workflows_db_postload_hooks', return_value=known):
            return [e['message'] for e in ai_workflows_graph_validate_trigger_config('event', {'hooks': hooks})]

    def test_duplicate_hooks_should_be_rejected(self):
        self.assertEqual(['Each event can only be selected once'],
                         self._errors(['on_postload_case_create', 'on_postload_case_create']))

    def test_more_hooks_than_known_should_be_rejected(self):
        hooks = ['on_postload_case_create', 'on_postload_alert_create', 'on_postload_x', 'on_postload_y']
        self.assertEqual(['At most 2 events'], self._errors(hooks))

    def test_unverifiable_hooks_should_be_rejected(self):
        with patch('app.iris_engine.ai_workflows.graph.ai_workflows_db_postload_hooks', side_effect=RuntimeError):
            errors = ai_workflows_graph_validate_trigger_config('event', {'hooks': ['on_postload_case_create']})
        self.assertEqual(['The events could not be verified, retry later'], [e['message'] for e in errors])

    def test_wildcard_should_not_need_the_known_hooks(self):
        with patch('app.iris_engine.ai_workflows.graph.ai_workflows_db_postload_hooks', side_effect=RuntimeError):
            self.assertEqual([], ai_workflows_graph_validate_trigger_config('event', {'hooks': ['*']}))
