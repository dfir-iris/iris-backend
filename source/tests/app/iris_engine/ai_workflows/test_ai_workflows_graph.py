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
from app.iris_engine.ai_workflows.graph import ai_workflows_graph_trigger_id
from app.iris_engine.ai_workflows.graph import ai_workflows_graph_upgrade_triggers
from app.iris_engine.ai_workflows.graph import ai_workflows_graph_validate
from app.iris_engine.ai_workflows.graph import ai_workflows_graph_validate_fragment
from app.iris_engine.ai_workflows.graph import ai_workflows_graph_validate_trigger_config

_WRITE_TOOL = 'iris_case_notes_create'
_READ_TOOL = 'iris_cases_get'


def _node(node_id, node_type, **config):
    return {'id': node_id, 'type': node_type, 'label': node_id, 'position': {'x': 0, 'y': 0}, 'config': config}


def _edge(source, target, port='out'):
    return {'id': f'{source}-{target}-{port}', 'source': source, 'target': target, 'source_port': port}


def _graph(nodes, edges, trigger_type='manual', **trigger_config):
    return {'nodes': [_node('trigger', 'trigger', trigger_type=trigger_type, **trigger_config)] + nodes,
            'edges': edges}


def _messages(errors):
    return [(e['node_id'], e['message']) for e in errors]


class TestsGraphStructure(TestCase):

    def _validate(self, graph, allowlist=None):
        return ai_workflows_graph_validate(graph, allowlist or [])

    def test_valid_graph_should_have_no_error(self):
        graph = _graph([_node('agent', 'ai_agent', prompt='Triage {{ entity.title }}', tools=[_READ_TOOL]),
                        _node('check', 'condition', rules=[{'path': 'nodes.agent.output.verdict',
                                                            'operator': 'eq', 'value': 'malicious'}]),
                        _node('note', 'action', tool=_WRITE_TOOL, arguments={'note_title': '{{ run.uuid }}'}),
                        _node('end', 'stop')],
                       [_edge('trigger', 'agent'), _edge('agent', 'check'), _edge('check', 'note', 'true'),
                        _edge('check', 'end', 'false')])
        self.assertEqual([], self._validate(graph, allowlist=[_WRITE_TOOL]))

    def test_graph_should_need_a_trigger(self):
        graph = {'nodes': [_node('a', 'stop')], 'edges': []}
        self.assertIn((None, 'A workflow needs a trigger node'), _messages(self._validate(graph)))

    def test_graph_should_hold_at_most_ten_triggers(self):
        graph = {'nodes': [_node(f't{i}', 'trigger', trigger_type='manual') for i in range(11)], 'edges': []}
        self.assertIn((None, 'A workflow has at most 10 trigger nodes'), _messages(self._validate(graph)))

    def test_unreachable_node_should_be_reported(self):
        graph = _graph([_node('orphan', 'stop')], [])
        self.assertEqual([('orphan', 'Not reachable from a trigger')], _messages(self._validate(graph)))

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
        self.assertIn(('trigger', 'Nothing can lead to a trigger'), _messages(self._validate(graph)))

    def test_note_should_need_no_edge(self):
        graph = _graph([_node('doc', 'note', text='Triage {{ not a template', color='blue', width=300, height=200)],
                       [])
        self.assertEqual([], self._validate(graph))

    def test_note_should_be_allowed_in_a_block(self):
        fragment = {'nodes': [_node('a', 'set_variables'), _node('doc', 'note', text='Why')], 'edges': []}
        self.assertEqual([], ai_workflows_graph_validate_fragment(fragment))

    def test_edge_into_or_out_of_a_note_should_be_reported(self):
        graph = _graph([_node('doc', 'note'), _node('end', 'stop')],
                       [_edge('trigger', 'doc'), _edge('doc', 'end'), _edge('trigger', 'end')])
        messages = _messages(self._validate(graph))
        self.assertIn(('doc', 'A note cannot be connected'), messages)
        self.assertIn(('doc', 'note nodes have no "out" output'), messages)

    def test_note_config_should_be_checked(self):
        graph = _graph([_node('doc', 'note', text=['x'], color='orange', width=10, height='tall')], [])
        fields = [(e['node_id'], e['field']) for e in self._validate(graph)]
        self.assertEqual([('doc', 'text'), ('doc', 'color'), ('doc', 'width'), ('doc', 'height')], fields)

    def test_cycle_without_waiting_node_should_be_allowed(self):
        graph = _graph([_node('a', 'set_variables'), _node('b', 'set_variables')],
                       [_edge('trigger', 'a'), _edge('a', 'b'), _edge('b', 'a')])
        self.assertEqual([], self._validate(graph))

    def test_cycle_through_a_waiting_node_should_be_allowed(self):
        for waiting in (_node('w', 'delay', minutes=5), _node('w', 'ask_analyst', question='?', fields=['x']),
                        _node('w', 'http_request', url='https://example.org', mode='async')):
            port = 'answered' if waiting['type'] == 'ask_analyst' else 'out'
            graph = _graph([_node('a', 'set_variables'), waiting],
                           [_edge('trigger', 'a'), _edge('a', 'w'), _edge('w', 'a', port)])
            self.assertEqual([], self._validate(graph), waiting['type'])

    def test_cycle_through_sync_http_or_zero_delay_should_be_allowed(self):
        for node in (_node('w', 'http_request', url='https://example.org'), _node('w', 'delay', minutes=0)):
            graph = _graph([_node('a', 'set_variables'), node],
                           [_edge('trigger', 'a'), _edge('a', 'w'), _edge('w', 'a')])
            self.assertEqual([], self._validate(graph), node['config'])

    def test_cycle_in_a_block_should_be_allowed(self):
        fragment = {'nodes': [_node('a', 'set_variables'), _node('b', 'set_variables')],
                    'edges': [_edge('a', 'b'), _edge('b', 'a')]}
        self.assertEqual([], ai_workflows_graph_validate_fragment(fragment))


class TestsGraphNodeConfig(TestCase):

    def _errors(self, node, allowlist=None, trigger_type='manual', trigger_config=None):
        graph = _graph([node], [_edge('trigger', node['id'])], trigger_type, **(trigger_config or {}))
        return [(e['field'], e['message']) for e in ai_workflows_graph_validate(graph, allowlist or [])]

    def test_action_with_a_write_tool_should_need_the_allowlist(self):
        node = _node('note', 'action', tool=_WRITE_TOOL)
        self.assertEqual('tool', self._errors(node)[0][0])
        self.assertEqual([], self._errors(node, allowlist=[_WRITE_TOOL]))

    def test_run_workflow_should_name_a_workflow_and_valid_inputs(self):
        node = _node('call', 'run_workflow', workflow_id=4, mode='wait', timeout_minutes=30,
                     inputs=[{'name': 'ioc', 'value': '{{ trigger.payload.data.ioc_value }}'},
                             {'name': 'data', 'value': {'$path': 'nodes.x.output'}}])
        self.assertEqual([], self._errors(node))
        node = _node('call', 'run_workflow', workflow_id='other', mode='wait')
        self.assertEqual(['workflow_id'], [field for field, _message in self._errors(node)])
        node = _node('call', 'run_workflow', workflow_id=None, mode='later', timeout_minutes=0,
                     inputs=[{'name': '1x', 'value': ''}, {'name': 'a', 'value': '{{'}, {'name': 'a', 'value': ''}])
        self.assertEqual(['workflow_id', 'mode', 'timeout_minutes', 'inputs.0.name', 'inputs.1.value',
                          'inputs.2.name'], [field for field, _message in self._errors(node)])

    def test_suggest_may_propose_a_write_tool_not_allowlisted(self):
        node = _node('s', 'suggest', title='Add a note', proposed_action={'tool': _WRITE_TOOL, 'arguments': {}})
        self.assertEqual([], self._errors(node))

    def test_suggest_may_bundle_several_write_tools(self):
        node = _node('s', 'suggest', title='Review', actions=[
            {'label': 'Save {{ vars.x }}', 'tool': _WRITE_TOOL, 'arguments': {'note_title': '{{ vars.x }}'}},
            {'label': 'Fix', 'tool': 'iris_cases_update', 'arguments': {'severity_id': {'$path': 'vars.id'}},
             'when': 'vars.fix == true'}])
        self.assertEqual([], self._errors(node))

    def test_suggest_actions_should_be_checked_one_by_one(self):
        node = _node('s', 'suggest', title='Review', actions=[
            {'label': 'Read', 'tool': _READ_TOOL, 'arguments': {}},
            {'label': '{{', 'tool': _WRITE_TOOL, 'arguments': ['x'], 'when': 'vars.x =='},
            'junk'])
        self.assertEqual(['actions.0.tool', 'actions.1.arguments', 'actions.1.label', 'actions.1.when', 'actions.2'],
                         [field for field, _message in self._errors(node)])

    def test_suggest_should_not_take_one_action_and_a_bundle(self):
        action = {'tool': _WRITE_TOOL, 'arguments': {}}
        node = _node('s', 'suggest', title='Review', proposed_action=action, actions=[action])
        self.assertEqual(['actions'], [field for field, _message in self._errors(node)])

    def test_suggest_should_bundle_ten_actions_at_most(self):
        node = _node('s', 'suggest', title='Review', actions=[{'tool': _WRITE_TOOL, 'arguments': {}}] * 11)
        self.assertEqual(['actions'], [field for field, _message in self._errors(node)])

    def test_unknown_tool_should_be_reported(self):
        self.assertEqual([('tools.0', 'Unknown tool nope')],
                         self._errors(_node('ai', 'ai_agent', prompt='x', tools=['nope'])))

    def test_case_access_writes_should_be_refused_to_agents_only(self):
        tool = 'iris_case_access_set_user'
        self.assertEqual([('tools.0', f'{tool} cannot be given to an agent: use an Action node')],
                         self._errors(_node('ai', 'ai_agent', prompt='x', tools=[tool]), allowlist=[tool]))
        self.assertEqual([], self._errors(_node('ai', 'ai_agent', prompt='x', tools=['iris_case_access_list'])))
        self.assertEqual([], self._errors(_node('a', 'action', tool=tool), allowlist=[tool]))
        self.assertEqual([], self._errors(_node('s', 'suggest', title='Share',
                                                proposed_action={'tool': tool, 'arguments': {}})))

    def test_allowlist_should_hold_write_tools_only(self):
        graph = _graph([], [])
        errors = ai_workflows_graph_validate(graph, [_READ_TOOL])
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

    def test_ask_analyst_should_check_its_audience(self):
        self.assertEqual('audience', self._errors(_node('ask', 'ask_analyst', question='?', fields=['x'],
                                                        audience='everyone'))[0][0])
        self.assertEqual([('user_ids', 'Select at least one user')],
                         self._errors(_node('ask', 'ask_analyst', question='?', fields=['x'], audience='users')))
        self.assertEqual([], self._errors(_node('ask', 'ask_analyst', question='?', fields=['x'], audience='users',
                                                user_ids=[5], popup=False)))
        self.assertEqual('popup', self._errors(_node('ask', 'ask_analyst', question='?', fields=['x'],
                                                     popup='yes'))[0][0])

    def test_suggest_should_check_its_audience(self):
        self.assertEqual([], self._errors(_node('s', 'suggest', title='t')))
        self.assertEqual('audience', self._errors(_node('s', 'suggest', title='t', audience='everyone'))[0][0])
        self.assertEqual([('user_ids', 'Select at least one user')],
                         self._errors(_node('s', 'suggest', title='t', audience='users')))
        self.assertEqual([], self._errors(_node('s', 'suggest', title='t', audience='users', user_ids=[5])))

    def test_http_request_should_check_method_and_url(self):
        errors = dict(self._errors(_node('h', 'http_request', method='TRACE')))
        self.assertIn('method', errors)
        self.assertEqual('Required', errors['url'])

    def test_oauth_client_credentials_should_need_an_https_token_url_and_a_client(self):
        node = _node('h', 'http_request', url='https://graph.example.org/x', auth_type='oauth2_client_credentials')
        self.assertEqual({'oauth_token_url', 'oauth_client_id', 'oauth_client_secret'}, set(dict(self._errors(node))))
        node['config'].update(oauth_token_url='http://login.example.org/{{ vars.tenant }}/token',
                              oauth_client_id='app', oauth_client_secret='{{ key("APP_SECRET") }}',
                              oauth_client_auth='header')
        errors = dict(self._errors(node))
        self.assertIn('https', errors['oauth_token_url'])
        self.assertIn('oauth_client_auth', errors)
        node['config'].update(oauth_token_url='https://login.example.org/{{ vars.tenant }}/token',
                              oauth_client_auth='basic')
        self.assertEqual([], self._errors(node))

    def test_war_room_members_add_should_be_refused_to_agents_only(self):
        tool = 'iris_war_room_members_add'
        self.assertEqual([('tools.0', f'{tool} cannot be given to an agent: use an Action node')],
                         self._errors(_node('ai', 'ai_agent', prompt='x', tools=[tool]), allowlist=[tool]))
        self.assertEqual([], self._errors(_node('a', 'action', tool=tool), allowlist=[tool]))

    def test_notify_about_should_name_an_entity_type_and_an_identifier(self):
        for about in (None, {}, {'entity_type': 'war_room', 'entity_id': 4},
                      {'entity_type': 'case', 'entity_id': '{{ vars.case_id }}'},
                      {'entity_type': 'alert', 'entity_id': {'$path': 'nodes.a.output.result.alert_id'}}):
            self.assertEqual([], self._errors(_node('n', 'notify', title='Hi', about=about)), about)
        cases = (
            ('x', ['about']),
            ({'entity_type': 'user', 'entity_id': 4}, ['about.entity_type']),
            ({'entity_id': 4}, ['about.entity_type']),
            ({'entity_type': 'war_room'}, ['about.entity_id']),
            ({'entity_type': 'war_room', 'entity_id': '{{'}, ['about.entity_id']),
            ({'entity_type': 'war_room', 'entity_id': {'$path': 'not a path'}}, ['about.entity_id']),
        )
        for about, fields in cases:
            errors = self._errors(_node('n', 'notify', title='Hi', about=about))
            self.assertEqual(fields, [field for field, _message in errors], about)

    def test_notify_users_should_be_picked_or_read_from_a_variable(self):
        for config in ({'user_ids': [3]}, {'users_from': '{{ vars.who }}'},
                       {'users_from': {'$path': 'nodes.a.output.result'}}, {'user_ids': [3], 'users_from': '{{ x }}'}):
            self.assertEqual([], self._errors(_node('n', 'notify', title='Hi', audience='users', **config)), config)
        cases = (
            ({}, ['user_ids']),
            ({'users_from': '{{'}, ['users_from']),
            ({'users_from': {'$path': 'not a path'}}, ['users_from']),
        )
        for config, fields in cases:
            errors = self._errors(_node('n', 'notify', title='Hi', audience='users', **config))
            self.assertEqual(fields, [field for field, _message in errors], config)

    def test_notify_delivery_severity_and_timeout_should_be_checked(self):
        self.assertEqual([], self._errors(_node('n', 'notify', title='Hi', delivery='both', severity='critical',
                                                timeout_seconds=0)))
        errors = self._errors(_node('n', 'notify', title='Hi', delivery='email', severity='high',
                                    timeout_seconds=-1))
        self.assertEqual(['delivery', 'severity', 'timeout_seconds'], [field for field, _message in errors])

    def test_cron_without_target_should_flag_entity_nodes(self):
        node = _node('related', 'find_related')
        errors = self._errors(node, trigger_type='cron', trigger_config={'cron': '* * * * *'})
        self.assertEqual([(None, 'Needs an entity, but a schedule without target leads here')], errors)
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

    def test_manual_menus_should_name_known_menus_the_trigger_accepts(self):
        self.assertEqual([], ai_workflows_graph_validate_trigger_config(
            'manual', {'entity_types': ['case', 'alert'], 'menus': ['alert', 'ioc'], 'menu_label': 'Enrich'}))
        errors = ai_workflows_graph_validate_trigger_config('manual', {'entity_types': ['case'], 'menus': ['moon']})
        self.assertEqual(['trigger_config.menus'], [e['field'] for e in errors])
        errors = ai_workflows_graph_validate_trigger_config('manual', {'entity_types': ['case'],
                                                                       'menus': ['ioc', 'ioc']})
        self.assertEqual(['A menu is listed twice'], [e['message'] for e in errors])
        errors = ai_workflows_graph_validate_trigger_config('manual', {'menus': ['alert', 'asset']})
        self.assertEqual(['The trigger must accept alert, case for these menus'], [e['message'] for e in errors])

    def test_manual_menu_label_should_be_a_short_string(self):
        for label in (12, 'x' * 65):
            errors = ai_workflows_graph_validate_trigger_config('manual', {'menu_label': label})
            self.assertEqual(['trigger_config.menu_label'], [e['field'] for e in errors])

    def test_manual_batch_should_be_a_boolean_on_case_object_menus(self):
        self.assertEqual([], ai_workflows_graph_validate_trigger_config(
            'manual', {'entity_types': ['case'], 'menus': ['asset', 'ioc'], 'batch': True}))
        errors = ai_workflows_graph_validate_trigger_config('manual', {'entity_types': ['case'], 'menus': ['asset'],
                                                                       'batch': 'yes'})
        self.assertEqual(['trigger_config.batch'], [e['field'] for e in errors])
        errors = ai_workflows_graph_validate_trigger_config('manual', {'entity_types': ['case', 'alert'],
                                                                       'menus': ['alert', 'case', 'ioc'],
                                                                       'batch': True})
        self.assertEqual(['One run for the whole selection is for objects of a case, not the alert, case menu'],
                         [e['message'] for e in errors])

    def test_unknown_trigger_type(self):
        self.assertEqual(['trigger_type'],
                         [e['field'] for e in ai_workflows_graph_validate_trigger_config('telepathy', {})])

    def test_workflow_trigger_should_have_no_config(self):
        self.assertEqual([], ai_workflows_graph_validate_trigger_config('workflow', {}))
        self.assertEqual(['trigger_config'],
                         [e['field'] for e in ai_workflows_graph_validate_trigger_config('workflow', {'x': 1})])


class TestsTriggerNodes(TestCase):

    def _errors(self, nodes, edges):
        return [(e['node_id'], e['field'], e['message']) for e in
                ai_workflows_graph_validate({'nodes': nodes, 'edges': edges}, [])]

    def test_several_triggers_should_pass(self):
        nodes = [_node('cron', 'trigger', trigger_type='cron', cron='0 * * * *', target='cases'),
                 _node('cron2', 'trigger', trigger_type='cron', cron='0 8 * * mon', target='war_rooms'),
                 _node('manual', 'trigger', trigger_type='manual', entity_types=['case']),
                 _node('call', 'trigger', trigger_type='workflow'),
                 _node('end', 'stop')]
        edges = [_edge('cron', 'end'), _edge('cron2', 'end'), _edge('manual', 'end'), _edge('call', 'end')]
        self.assertEqual([], self._errors(nodes, edges))

    def test_trigger_node_should_have_a_type(self):
        nodes = [_node('t', 'trigger')]
        self.assertEqual([('t', 'trigger_type', 'Select what starts the run')], self._errors(nodes, []))

    def test_config_errors_should_point_at_the_node_key(self):
        nodes = [_node('t', 'trigger', trigger_type='cron', cron='61 * * * *'),
                 _node('u', 'trigger', trigger_type='telepathy'),
                 _node('w', 'trigger', trigger_type='workflow', x=1)]
        self.assertEqual([('t', 'cron'), ('u', 'trigger_type'), ('w', 'config')],
                         [error[:2] for error in self._errors(nodes, [])])

    def test_webhook_and_workflow_triggers_should_appear_once(self):
        nodes = [_node('w1', 'trigger', trigger_type='workflow'), _node('w2', 'trigger', trigger_type='workflow')]
        self.assertEqual([('w2', 'trigger_type', 'A workflow has at most one workflow trigger')],
                         self._errors(nodes, []))

    def test_workflow_calling_itself_should_have_a_workflow_trigger(self):
        nodes = [_node('manual', 'trigger', trigger_type='manual'),
                 _node('again', 'run_workflow', workflow_id='self', mode='async'), _node('end', 'stop')]
        edges = [_edge('manual', 'again'), _edge('again', 'end')]
        self.assertEqual([('again', 'workflow_id', 'Calls this workflow, which needs a "Called by a workflow" '
                                                   'trigger')], self._errors(nodes, edges))
        nodes.append(_node('call', 'trigger', trigger_type='workflow'))
        edges.append(_edge('call', 'end'))
        self.assertEqual([], self._errors(nodes, edges))

    def test_entityless_schedule_should_flag_only_what_it_reaches(self):
        nodes = [_node('cron', 'trigger', trigger_type='cron', cron='* * * * *'),
                 _node('manual', 'trigger', trigger_type='manual', entity_types=['case']),
                 _node('related', 'find_related'), _node('end', 'stop')]
        self.assertEqual([], self._errors(nodes, [_edge('cron', 'end'), _edge('manual', 'related')]))
        self.assertEqual([('related', None, 'Needs an entity, but a schedule without target leads here')],
                         self._errors(nodes, [_edge('cron', 'related'), _edge('manual', 'end')]))

    def test_node_reachable_from_any_trigger_should_pass(self):
        nodes = [_node('a', 'trigger', trigger_type='manual'), _node('b', 'trigger', trigger_type='manual'),
                 _node('x', 'stop'), _node('y', 'stop')]
        self.assertEqual([], self._errors(nodes, [_edge('a', 'x'), _edge('b', 'y')]))

    def test_trigger_id_should_default_to_the_first(self):
        graph = {'nodes': [_node('end', 'stop'), _node('a', 'trigger'), _node('b', 'trigger')], 'edges': []}
        self.assertEqual('b', ai_workflows_graph_trigger_id(graph, 'b'))
        self.assertEqual('a', ai_workflows_graph_trigger_id(graph, 'end'))
        self.assertEqual('a', ai_workflows_graph_trigger_id(graph))
        self.assertIsNone(ai_workflows_graph_trigger_id({'nodes': [], 'edges': []}))


class TestsUpgradeTriggers(TestCase):

    _LEGACY = {'nodes': [_node('trigger', 'trigger'), _node('end', 'stop')], 'edges': [_edge('trigger', 'end')]}

    def test_legacy_graph_should_get_a_node_per_trigger(self):
        graph = ai_workflows_graph_upgrade_triggers(self._LEGACY, [
            {'type': 'manual', 'config': {'entity_types': ['case']}},
            {'type': 'cron', 'config': {'cron': '0 * * * *'}},
        ])
        self.assertEqual({'trigger_type': 'manual', 'entity_types': ['case']}, graph['nodes'][0]['config'])
        added = graph['nodes'][2]
        self.assertEqual(('n1', 'trigger', {'trigger_type': 'cron', 'cron': '0 * * * *'}, 120),
                         (added['id'], added['type'], added['config'], added['position']['y']))
        self.assertEqual([('trigger', 'end'), ('n1', 'end')], [(e['source'], e['target']) for e in graph['edges']])
        self.assertEqual(['e1'], [e['id'] for e in graph['edges'][1:]])
        self.assertEqual([], ai_workflows_graph_validate(graph, []))
        self.assertEqual({}, self._LEGACY['nodes'][0]['config'])

    def test_typed_or_triggerless_graph_should_be_left_alone(self):
        typed = _graph([], [])
        self.assertIs(typed, ai_workflows_graph_upgrade_triggers(typed, [{'type': 'cron', 'config': {}}]))
        self.assertIs(self._LEGACY, ai_workflows_graph_upgrade_triggers(self._LEGACY, []))
        self.assertIsNone(ai_workflows_graph_upgrade_triggers(None, [{'type': 'manual', 'config': {}}]))


class TestsGraphLimits(TestCase):

    def _messages(self, graph):
        return [(e['node_id'], e['field'], e['message']) for e in ai_workflows_graph_validate(graph, [])]

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

    def test_handles_should_name_sides(self):
        node = _node('end', 'stop')
        node['handles'] = {'input': 'top', 'output': 'bottom'}
        self.assertEqual([], self._messages(_graph([node], [_edge('trigger', 'end')])))
        for handles in ({'input': 'middle'}, {'side': 'top'}, 'top'):
            node['handles'] = handles
            messages = self._messages(_graph([node], [_edge('trigger', 'end')]))
            self.assertEqual([('end', 'handles')], [m[:2] for m in messages])

    def test_edge_sides_should_name_sides(self):
        edge = _edge('trigger', 'end')
        edge.update({'source_side': 'bottom', 'target_side': 'top'})
        self.assertEqual([], self._messages(_graph([_node('end', 'stop')], [edge])))
        edge['target_side'] = 'middle'
        messages = self._messages(_graph([_node('end', 'stop')], [edge]))
        self.assertIn('edges.0.target_side', [m[1] for m in messages])

    def test_reserved_node_ids_should_be_refused(self):
        for node_id in ('__proto__', 'constructor', 'prototype'):
            messages = self._messages(_graph([_node(node_id, 'stop')], [_edge('trigger', node_id)]))
            self.assertIn('nodes.1.id', [m[1] for m in messages], node_id)


class TestsGraphSecurityChecks(TestCase):

    def _errors(self, node):
        graph = _graph([node], [_edge('trigger', node['id'])])
        return [(e['field'], e['message']) for e in ai_workflows_graph_validate(graph, [])]

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
