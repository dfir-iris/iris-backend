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

"""The HTTP request node of AI workflows: keystore values only in the
request, a destination data cannot change, the instance's own AI
workflow endpoints refused, dry runs that send nothing, and masked
errors. Runs on the in-memory engine harness."""

import json
from types import SimpleNamespace
from unittest.mock import patch

from app import app
from tests.app.iris_engine.ai_workflows.harness import EngineTestCase
from tests.app.iris_engine.ai_workflows.harness import OWNER_ID
from tests.app.iris_engine.ai_workflows.harness import SECRET_NAME
from tests.app.iris_engine.ai_workflows.harness import SECRET_VALUE
from tests.app.iris_engine.ai_workflows.harness import chain
from tests.app.iris_engine.ai_workflows.harness import workflow

_KEY = f'{{{{ key("{SECRET_NAME}") }}}}'


def _http(**config):
    return {'id': 'http', 'type': 'http_request', 'config': {'use_proxy': False, **config}}


def _vars(**values):
    return {'id': 'vars', 'type': 'set_variables',
            'config': {'variables': [{'name': k, 'value': v} for k, v in values.items()]}}


class _HttpTestCase(EngineTestCase):

    def setUp(self):
        super().setUp()
        self.sent = []
        self.response = {'success': True, 'status_code': 200, 'response_headers': {}, 'response_body': '{}'}
        patcher = patch('app.iris_engine.ai_workflows.nodes.webhooks_send', self._send)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _send(self, request, **kwargs):
        self.sent.append((request, kwargs))
        return dict(self.response)

    def run_nodes(self, *nodes, **kwargs):
        return self.run_to_rest(workflow(chain(*nodes)), **kwargs)

    def http_step(self, run):
        return [s for s in self.steps(run) if s.node_id == 'http'][0]

    def persisted(self, run):
        return json.dumps([run.context, run.error, [(s.input, s.output, s.error) for s in self.steps(run)]],
                          default=str)


class TestsHttpKeys(_HttpTestCase):

    def test_key_should_be_a_placeholder_outside_http_requests(self):
        run = self.run_nodes(_vars(tok=f'Bearer {_KEY}'))
        self.assertEqual({'tok': f'Bearer [secret:{SECRET_NAME}]'}, run.context['vars'])

    def test_key_should_be_sent_but_never_persisted(self):
        run = self.run_nodes(_http(url='https://api.example.org/', auth_type='bearer', auth_secret=_KEY,
                                   body_mode='template', body_template=f'{{"token": "{_KEY}"}}'))
        request, _kwargs = self.sent[0]
        self.assertEqual(f'Bearer {SECRET_VALUE}', request['headers']['Authorization'])
        self.assertNotIn(SECRET_VALUE, self.persisted(run))


class TestsHttpDestination(_HttpTestCase):

    def test_url_values_should_be_percent_encoded(self):
        self.run_nodes(_vars(p='../../admin?x=1#f'), _http(url='https://api.example.org/items/{{ vars.p }}'))
        self.assertEqual('https://api.example.org/items/..%2F..%2Fadmin%3Fx%3D1%23f', self.sent[0][0]['url'])

    def test_templated_host_should_be_refused_at_runtime(self):
        for url in ('https://{{ vars.h }}/x', '{{ vars.u }}', 'https://api.example.org{{ vars.h }}/'):
            self.sent = []
            run = self.run_nodes(_vars(h='evil.example', u='https://evil.example/'), _http(url=url))
            self.assertEqual([], self.sent, url)
            self.assertEqual('failed', run.status, url)

    def test_host_may_come_from_a_key(self):
        # The harness key value doubles as a host name
        run = self.run_nodes(_vars(p='a/b'), _http(url=f'https://{_KEY}:8089/x/{{{{ vars.p }}}}'))
        self.assertEqual(f'https://{SECRET_VALUE}:8089/x/a%2Fb', self.sent[0][0]['url'])
        self.assertNotIn(SECRET_VALUE, self.persisted(run))

    def test_inbound_hooks_should_be_refused_on_any_host(self):
        for url in ('https://other.example/api/v2/ai-workflows/hooks/abc',
                    'https://other.example/x/..%2Fapi/v2/AI-workflows/hooks/abc',
                    'https://other.example/a/%252e%252e/api/v2/ai-workflows/hooks'):
            self.sent = []
            run = self.run_nodes(_http(url=url))
            self.assertEqual([], self.sent, url)
            self.assertIn('inbound hooks', self.http_step(run).error, url)

    def test_own_callbacks_should_be_refused(self):
        url = 'https://iris.example.org/api/v2/ai-workflows/callbacks/0b5f'
        with patch.dict(app.config, {'AI_WORKFLOWS_CALLBACK_BASE_URL': 'https://IRIS.example.org/'}):
            run = self.run_nodes(_http(url=url))
            self.assertEqual([], self.sent)
            self.assertIn('callbacks of this instance', self.http_step(run).error)
            self.run_nodes(_http(url='https://soar.example.net/api/v2/ai-workflows/callbacks/0b5f'))
        self.assertEqual(1, len(self.sent))


class TestsHttpDryRun(_HttpTestCase):

    def test_dry_run_should_not_send_and_show_the_masked_request(self):
        run = self.run_nodes(_http(url='https://api.example.org/', mode='async', auth_type='bearer',
                                   auth_secret=_KEY, body_mode='template', body_template='{"a": 1}'),
                             dry_run=True)
        self.assertEqual([], self.sent)
        output = self.http_step(run).output
        self.assertTrue(output['dry_run'])
        self.assertEqual('https://api.example.org/', output['request']['url'])
        self.assertEqual('{"a": 1}', output['request']['body'])
        self.assertEqual([], list(self.store.waits.values()))
        self.assertNotIn(SECRET_VALUE, self.persisted(run))


class TestsHttpErrors(_HttpTestCase):

    def test_transport_error_should_be_masked(self):
        self.response = {'success': False, 'status_code': None, 'response_headers': {}, 'response_body': None,
                         'error': f'Connection refused for token {SECRET_VALUE}'}
        run = self.run_nodes(_http(url='https://api.example.org/', auth_type='bearer', auth_secret=_KEY))
        self.assertEqual(1, len(self.sent))
        self.assertNotIn(SECRET_VALUE, self.persisted(run))

    def test_render_error_should_be_masked(self):
        run = self.run_nodes(_http(url='https://api.example.org/', body_mode='template',
                                   body_template=f'{_KEY}{{{{ 9 ** 99999 }}}}'))
        self.assertEqual([], self.sent)
        self.assertNotIn(SECRET_VALUE, self.persisted(run))


class TestsHttpResponse(_HttpTestCase):

    def test_large_json_answer_should_be_read_whole_and_parsed(self):
        self.response = dict(self.response, response_body=json.dumps({'data': {'pad': 'x' * 40000, 'id': '1.1.1.1'}}))
        run = self.run_nodes(_http(url='https://api.example.org/', response_format='json'))
        self.assertGreater(self.sent[0][1]['max_response_chars'], 40000)
        self.assertEqual('1.1.1.1', self.http_step(run).output['body']['data']['id'])

    def test_large_text_answer_should_be_cut(self):
        self.response = dict(self.response, response_body='x' * 100000)
        run = self.run_nodes(_http(url='https://api.example.org/', response_format='text'))
        body = self.http_step(run).output['body']
        self.assertLess(len(body), 70000)
        self.assertIn('truncated', body)


class TestsNotifyAudience(EngineTestCase):

    def test_users_audience_without_entity_should_skip_inactive_and_unknown_users(self):
        users = {5: SimpleNamespace(id=5, active=True), 6: SimpleNamespace(id=6, active=False)}
        node = {'id': 'notify', 'type': 'notify', 'config': {'audience': 'users', 'user_ids': [5, 6, 7],
                                                              'title': 'Hello'}}
        with patch('app.iris_engine.ai_workflows.nodes.ai_workflows_db_get_user', users.get), \
                patch('app.iris_engine.notifications.service.notify_many') as notify_many:
            run = self.run_to_rest(workflow(chain(node)), entity_type=None, entity_id=None)
        self.assertEqual('succeeded', run.status)
        self.assertEqual([5], notify_many.call_args.args[0])

    def _notify_entity(self, owners, reachable, actor_id=None, audience='entity', triggered_by=None):
        node = {'id': 'notify', 'type': 'notify', 'config': {'audience': audience, 'title': 'Hello'}}
        wf = workflow(chain(node))
        with patch('app.iris_engine.ai_workflows.nodes.ai_workflows_db_entity_owner_ids', lambda *_args: owners), \
                patch('app.iris_engine.ai_workflows.nodes.ai_workflows_entities_user_can_access',
                      lambda user_id, *_args: user_id in reachable), \
                patch('app.iris_engine.notifications.service.notify_many') as notify_many:
            run = self.start(wf)
            # An event run unless `triggered_by` started it by hand
            run.triggered_by_user_id = triggered_by
            run.trigger_payload = {'actor': {'id': actor_id}} if actor_id else None
            from app.iris_engine.ai_workflows.engine import ai_workflows_engine_step
            while run.id in self.enqueued:
                self.enqueued.remove(run.id)
                ai_workflows_engine_step(run.id)
        step = [s for s in self.steps(run) if s.node_id == 'notify'][0]
        return notify_many, step.output

    def test_entity_audience_should_add_the_user_behind_the_event(self):
        notify_many, output = self._notify_entity([8], {8, 9}, actor_id=9)
        self.assertEqual([8, 9], notify_many.call_args.args[0])
        self.assertEqual([8, 9], output['notified'])
        self.assertNotIn('note', output)

    def test_entity_audience_should_fall_back_to_the_workflow_owner(self):
        notify_many, output = self._notify_entity([8], {OWNER_ID}, actor_id=9)
        self.assertEqual([OWNER_ID], notify_many.call_args.args[0])
        self.assertIn('workflow owner', output['note'])

    def test_triggerer_audience_should_notify_only_the_user_who_started_the_run(self):
        notify_many, output = self._notify_entity([8], {8, 9}, audience='triggerer', triggered_by=9)
        self.assertEqual([9], notify_many.call_args.args[0])
        self.assertEqual([9], output['notified'])

    def test_triggerer_audience_should_notify_the_user_behind_the_event(self):
        notify_many, _output = self._notify_entity([8], {8, 9}, actor_id=9, audience='triggerer')
        self.assertEqual([9], notify_many.call_args.args[0])

    def test_triggerer_audience_should_not_fall_back_to_the_workflow_owner(self):
        notify_many, output = self._notify_entity([8], {8, OWNER_ID}, audience='triggerer', triggered_by=9)
        notify_many.assert_not_called()
        self.assertIn('None of the users', output['note'])

    def test_unreachable_audience_should_notify_nobody_and_say_why(self):
        node = {'id': 'notify', 'type': 'notify', 'config': {'audience': 'owner', 'title': 'Hello'}}
        with patch('app.iris_engine.ai_workflows.nodes.ai_workflows_entities_user_can_access', lambda *_args: False), \
                patch('app.iris_engine.notifications.service.notify_many') as notify_many:
            run = self.run_to_rest(workflow(chain(node)))
        self.assertEqual('succeeded', run.status)
        notify_many.assert_not_called()
        step = [s for s in self.steps(run) if s.node_id == 'notify'][0]
        self.assertEqual([], step.output['notified'])
        self.assertIn('None of the users', step.output['note'])


class TestsNotifyUsersFrom(EngineTestCase):
    """`users_from`: the users to notify read from the run context."""

    def _notify(self, users_from, user_ids=(), names=None):
        users = {u: SimpleNamespace(id=u, active=True) for u in (5, 6, 7)}
        node = {'id': 'notify', 'type': 'notify', 'config': {'audience': 'users', 'user_ids': list(user_ids),
                                                              'users_from': users_from, 'title': 'Hello'}}
        lookup = names if names is not None else {'alice': 6, 'bob@example.org': 7}
        with patch('app.iris_engine.ai_workflows.nodes.ai_workflows_db_get_user', users.get), \
                patch('app.iris_engine.ai_workflows.nodes.ai_workflows_db_user_ids_by_names',
                      lambda wanted: {n: lookup[n] for n in wanted if n in lookup}), \
                patch('app.iris_engine.notifications.service.notify_many') as notify_many:
            run = self.run_to_rest(workflow(chain(_vars(ids='5', who='alice, bob@example.org'), node)),
                                   entity_type=None, entity_id=None)
        step = [s for s in self.steps(run) if s.node_id == 'notify'][0]
        return run, notify_many, step.output

    def test_template_should_give_ids_logins_and_emails(self):
        run, notify_many, output = self._notify('{{ vars.who }}, {{ vars.ids }}')
        self.assertEqual('succeeded', run.status)
        self.assertEqual([5, 6, 7], notify_many.call_args.args[0])
        self.assertNotIn('note', output)

    def test_path_should_give_the_value_as_is(self):
        _run, notify_many, _output = self._notify({'$path': 'vars.ids'})
        self.assertEqual([5], notify_many.call_args.args[0])

    def test_list_should_hold_ids_names_or_users(self):
        from app.iris_engine.ai_workflows.nodes import _notify_users_from

        ctx = SimpleNamespace(template_context=dict)
        value = [5, '6', 'alice', {'user_id': 8}, {'id': 9}, True, None, 'nobody']
        with patch('app.iris_engine.ai_workflows.nodes.ai_workflows_context_render_arguments',
                   lambda *_args: value), \
                patch('app.iris_engine.ai_workflows.nodes.ai_workflows_db_user_ids_by_names',
                      lambda wanted: {'alice': 7} if 'alice' in wanted else {}):
            self.assertEqual(([5, 6, 8, 9, 7], ['nobody']), _notify_users_from(ctx, {'users_from': {'$path': 'x'}}))

    def test_picked_users_should_add_to_those_of_the_variable(self):
        _run, notify_many, _output = self._notify('{{ vars.ids }}', user_ids=[7])
        self.assertEqual([7, 5], notify_many.call_args.args[0])

    def test_unknown_names_should_be_reported(self):
        _run, notify_many, output = self._notify('{{ vars.who }}', names={'alice': 6})
        self.assertEqual([6], notify_many.call_args.args[0])
        self.assertIn('bob@example.org', output['note'])


class TestsNotifyDelivery(EngineTestCase):

    def _notify(self, **config):
        users = {5: SimpleNamespace(id=5, active=True)}
        node = {'id': 'notify', 'type': 'notify', 'config': {'audience': 'users', 'user_ids': [5],
                                                              'title': 'Hello', **config}}
        with patch('app.iris_engine.ai_workflows.nodes.ai_workflows_db_get_user', users.get), \
                patch('app.iris_engine.notifications.service.notify_many') as notify_many, \
                patch('app.iris_engine.notifications.service.toast_many') as toast_many:
            run = self.run_to_rest(workflow(chain(node)), entity_type=None, entity_id=None)
        step = [s for s in self.steps(run) if s.node_id == 'notify'][0]
        return notify_many, toast_many, step.output

    def test_default_should_be_a_notification(self):
        notify_many, toast_many, output = self._notify()
        self.assertIsNone(notify_many.call_args.kwargs['severity'])
        toast_many.assert_not_called()
        self.assertEqual('notification', output['delivery'])

    def test_notification_should_carry_the_severity(self):
        notify_many, _toast_many, output = self._notify(severity='critical')
        self.assertEqual('critical', notify_many.call_args.kwargs['severity'])
        self.assertEqual('critical', output['severity'])

    def test_toast_should_not_store_a_notification(self):
        notify_many, toast_many, _output = self._notify(delivery='toast', severity='warning', timeout_seconds=0)
        notify_many.assert_not_called()
        self.assertEqual([5], toast_many.call_args.args[0])
        self.assertEqual('warning', toast_many.call_args.kwargs['severity'])
        self.assertEqual(0, toast_many.call_args.kwargs['timeout_seconds'])

    def test_both_should_notify_and_toast(self):
        notify_many, toast_many, _output = self._notify(delivery='both', timeout_seconds=99999)
        notify_many.assert_called_once()
        self.assertEqual(3600, toast_many.call_args.kwargs['timeout_seconds'])


class TestsNotifyAbout(EngineTestCase):
    """A notification about an entity of the run's scope other than its
    entity: a war room a webhook run created."""

    def _notify_about(self, about, reachable=frozenset({8}), checked=None, owners=(8, 9)):
        node = {'id': 'notify', 'type': 'notify', 'config': {'audience': 'entity', 'title': 'Hello',
                                                              'about': about}}
        checks = []

        def _check(run, key, value):
            checks.append((key, value))
            if checked is not None:
                checked(key, value)

        with patch('app.iris_engine.ai_workflows.nodes.ai_workflows_agent_check_identifier', _check), \
                patch('app.iris_engine.ai_workflows.nodes.ai_workflows_db_entity_owner_ids',
                      lambda entity_type, entity_id: owners if (entity_type, entity_id) == ('war_room', 11) else ()), \
                patch('app.iris_engine.ai_workflows.nodes.ai_workflows_entities_user_can_access',
                      lambda user_id, entity_type, entity_id: (entity_type, entity_id) == ('war_room', 11)
                      and user_id in reachable), \
                patch('app.iris_engine.notifications.service.notify_many') as notify_many:
            run = self.run_to_rest(workflow(chain(_vars(room='11'), node)), entity_type=None, entity_id=None)
        return run, notify_many, checks

    def test_entityless_run_should_notify_about_a_war_room_of_its_scope(self):
        run, notify_many, checks = self._notify_about({'entity_type': 'war_room',
                                                       'entity_id': {'$path': 'vars.room'}})
        self.assertEqual('succeeded', run.status)
        self.assertEqual([('war_room_id', 11)], checks)
        self.assertEqual([8], notify_many.call_args.args[0])
        self.assertEqual('/war-rooms/11', notify_many.call_args.kwargs['link'])
        step = [s for s in self.steps(run) if s.node_id == 'notify'][0]
        self.assertEqual({'entity_type': 'war_room', 'entity_id': 11}, step.output['about'])

    def test_template_identifier_should_be_accepted(self):
        run, notify_many, _checks = self._notify_about({'entity_type': 'war_room', 'entity_id': '{{ vars.room }}'})
        self.assertEqual('succeeded', run.status)
        self.assertEqual('/war-rooms/11', notify_many.call_args.kwargs['link'])

    def test_entity_outside_the_run_scope_should_fail_the_node(self):
        from app.iris_engine.ai_workflows.agent import AiWorkflowScopeError

        def _refuse(_key, _value):
            raise AiWorkflowScopeError('war_room_id=11: this run is not about that war room')

        run, notify_many, _checks = self._notify_about({'entity_type': 'war_room', 'entity_id': 11}, checked=_refuse)
        self.assertEqual('failed', run.status)
        notify_many.assert_not_called()

    def test_identifier_that_is_not_an_id_should_fail_the_node(self):
        for value in ('vpn', 0, True, '{{ vars.missing }}'):
            run, notify_many, _checks = self._notify_about({'entity_type': 'war_room', 'entity_id': value})
            self.assertEqual('failed', run.status, value)
            notify_many.assert_not_called()

    def test_other_entity_types_should_fail_the_node(self):
        run, notify_many, _checks = self._notify_about({'entity_type': 'user', 'entity_id': 11})
        self.assertEqual('failed', run.status)
        notify_many.assert_not_called()


class TestsSuggestAudience(EngineTestCase):

    def _suggest(self, config, reachable, dry_run=False):
        node = {'id': 'suggest', 'type': 'suggest', 'config': {'title': 'Look at this', **config}}
        with patch('app.iris_engine.ai_workflows.nodes.ai_workflows_entities_user_can_access',
                   lambda user_id, *_args: user_id in reachable):
            run = self.run_to_rest(workflow(chain(node)), dry_run=dry_run)
        self.assertEqual('succeeded', run.status)
        step = [s for s in self.steps(run) if s.node_id == 'suggest'][0]
        return self.store.suggestions[-1], step.output

    def test_default_audience_should_be_the_workflow_suggestion_audience(self):
        suggestion, output = self._suggest({}, {5})
        self.assertNotIn('audience_user_ids', vars(suggestion))
        self.assertEqual([], output['audience'])
        self.assertNotIn('note', output)

    def test_users_audience_should_address_the_reachable_users(self):
        suggestion, output = self._suggest({'audience': 'users', 'user_ids': [5, 6, 5]}, {5})
        self.assertEqual([5], suggestion.audience_user_ids)
        self.assertEqual([5], output['audience'])
        self.assertEqual(suggestion.id, output['suggestion_id'])

    def test_owner_audience_should_address_the_workflow_owner(self):
        suggestion, _output = self._suggest({'audience': 'owner'}, {OWNER_ID})
        self.assertEqual([OWNER_ID], suggestion.audience_user_ids)

    def test_unreachable_audience_should_fall_back_and_say_why(self):
        suggestion, output = self._suggest({'audience': 'users', 'user_ids': [5]}, set())
        self.assertNotIn('audience_user_ids', vars(suggestion))
        self.assertIn('the suggestion went to the workflow suggestion audience', output['note'])

    def test_dry_run_should_ignore_the_audience(self):
        suggestion, output = self._suggest({'audience': 'users', 'user_ids': [5]}, {5}, dry_run=True)
        self.assertNotIn('audience_user_ids', vars(suggestion))
        self.assertNotIn('note', output)
