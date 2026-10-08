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

import hashlib
import hmac
import time
from unittest import TestCase
from urllib import parse
from uuid import uuid4

import requests

from iris import API_URL
from iris import Iris

_IDENTIFIER_FOR_NONEXISTENT_OBJECT = 123456789
_AI_WORKFLOWS = '/api/v2/ai-workflows'
_AI_SUGGESTIONS = '/api/v2/ai-suggestions'
_AI_WORKFLOWS_READ = 0x80000000
_AI_WORKFLOWS_WRITE = 0x100000000
_IRIS_PERMISSION_ALERTS_READ = 0x4
_TERMINAL_OR_WAITING = ('succeeded', 'failed', 'cancelled', 'skipped', 'waiting')


def _graph():
    return {
        'nodes': [
            {'id': 'n1', 'type': 'trigger', 'label': 'Run', 'position': {'x': 0, 'y': 0}, 'config': {}},
            {'id': 'n2', 'type': 'set_variables', 'label': 'Vars', 'position': {'x': 0, 'y': 100},
             'config': {'variables': [{'name': 'title', 'value': '{{ entity.alert_title }}'}]}},
            {'id': 'n3', 'type': 'stop', 'label': 'Done', 'position': {'x': 0, 'y': 200},
             'config': {'status': 'succeeded', 'reason': 'done'}},
        ],
        'edges': [
            {'id': 'e1', 'source': 'n1', 'target': 'n2', 'source_port': 'out'},
            {'id': 'e2', 'source': 'n2', 'target': 'n3', 'source_port': 'out'},
        ],
    }


def _body(**overrides):
    body = {
        'name': f'workflow {uuid4()}',
        'description': 'Test workflow',
        'is_active': True,
        'trigger_type': 'manual',
        'trigger_config': {'entity_types': ['alert']},
        'customer_scope': [],
        'graph': _graph(),
        'write_tool_allowlist': [],
        'max_runs_per_hour': 60,
        'token_budget_per_run': 50000,
        'suggestion_audience': 'entity',
    }
    body.update(overrides)
    return body


def _post_raw(path, data, headers):
    return requests.post(parse.urljoin(API_URL, path), data=data, headers=headers)


def _signed_headers(token, signing_secret, raw_body, timestamp=None):
    timestamp = str(int(time.time())) if timestamp is None else timestamp
    digest = hmac.new(signing_secret.encode(), f'{timestamp}.'.encode() + raw_body, hashlib.sha256).hexdigest()
    return {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json',
            'X-IRIS-Timestamp': timestamp, 'X-IRIS-Signature': f'sha256={digest}'}


class TestsRestAiWorkflows(TestCase):

    def setUp(self) -> None:
        self._subject = Iris()

    def tearDown(self):
        # The owner FK is RESTRICT: workflows go before the users
        workflows = self._subject.get(_AI_WORKFLOWS).json()
        if isinstance(workflows, list):
            for workflow in workflows:
                self._subject.delete(f"{_AI_WORKFLOWS}/{workflow['id']}")
        self._subject.clear_database()

    def _create_workflow(self, **overrides):
        return self._subject.create(_AI_WORKFLOWS, _body(**overrides))

    def _create_alert(self):
        body = {
            'alert_title': 'AI workflow alert',
            'alert_severity_id': 4,
            'alert_status_id': 3,
            'alert_customer_id': 1,
        }
        return self._subject.create('/api/v2/alerts', body).json()['alert_id']

    def _wait_for_run(self, run_uuid, timeout=60):
        deadline = time.monotonic() + timeout
        run = None
        while time.monotonic() < deadline:
            run = self._subject.get(f'{_AI_WORKFLOWS}/runs/{run_uuid}').json()
            if run.get('status') in _TERMINAL_OR_WAITING:
                return run
            time.sleep(1)
        return run

    # ---- CRUD ----------------------------------------------------------

    def test_create_workflow_should_return_201(self):
        response = self._create_workflow()
        self.assertEqual(201, response.status_code)

    def test_create_workflow_should_return_version_1_and_the_owner(self):
        body = self._create_workflow().json()
        self.assertEqual(1, body['version'])
        self.assertEqual(1, body['owner_id'])
        self.assertIn('uuid', body)

    def test_get_workflow_should_return_200(self):
        identifier = self._create_workflow().json()['id']
        response = self._subject.get(f'{_AI_WORKFLOWS}/{identifier}')
        self.assertEqual(200, response.status_code)

    def test_get_unknown_workflow_should_return_404(self):
        response = self._subject.get(f'{_AI_WORKFLOWS}/{_IDENTIFIER_FOR_NONEXISTENT_OBJECT}')
        self.assertEqual(404, response.status_code)

    def test_list_workflows_should_include_the_created_one(self):
        identifier = self._create_workflow().json()['id']
        workflows = self._subject.get(_AI_WORKFLOWS).json()
        self.assertIn(identifier, [w['id'] for w in workflows])

    def test_update_graph_should_create_a_new_version(self):
        identifier = self._create_workflow().json()['id']
        graph = _graph()
        graph['nodes'][2]['config']['reason'] = 'changed'
        response = self._subject.update(f'{_AI_WORKFLOWS}/{identifier}', {'graph': graph, 'version_note': 'v2'})
        self.assertEqual(200, response.status_code)
        self.assertEqual(2, response.json()['version'])
        versions = self._subject.get(f'{_AI_WORKFLOWS}/{identifier}/versions').json()
        self.assertEqual([1, 2], sorted(v['version'] for v in versions))

    def test_get_version_should_return_the_snapshot(self):
        identifier = self._create_workflow().json()['id']
        response = self._subject.get(f'{_AI_WORKFLOWS}/{identifier}/versions/1')
        self.assertEqual(200, response.status_code)
        self.assertEqual('manual', response.json()['snapshot']['trigger_type'])

    def test_delete_workflow_should_return_204(self):
        identifier = self._create_workflow().json()['id']
        response = self._subject.delete(f'{_AI_WORKFLOWS}/{identifier}')
        self.assertEqual(204, response.status_code)
        self.assertEqual(404, self._subject.get(f'{_AI_WORKFLOWS}/{identifier}').status_code)

    def test_catalogue_should_never_return_an_llm_key(self):
        response = self._subject.get(f'{_AI_WORKFLOWS}/catalogue')
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual({'enabled', 'provider', 'model'}, set(body['llm']))
        self.assertIn('trigger', [n['type'] for n in body['node_types']])

    # ---- Validation ----------------------------------------------------

    def test_create_workflow_without_name_should_return_400(self):
        response = self._create_workflow(name='')
        self.assertEqual(400, response.status_code)
        self.assertIn('name', [e['field'] for e in response.json()['data']['errors']])

    def test_create_workflow_with_invalid_trigger_type_should_return_400(self):
        response = self._create_workflow(trigger_type='bogus')
        self.assertEqual(400, response.status_code)

    def test_create_workflow_without_trigger_node_should_return_400(self):
        graph = _graph()
        graph['nodes'] = graph['nodes'][1:]
        graph['edges'] = graph['edges'][1:]
        response = self._create_workflow(graph=graph)
        self.assertEqual(400, response.status_code)

    def test_create_workflow_with_read_tool_in_allowlist_should_return_400(self):
        tools = self._subject.get(f'{_AI_WORKFLOWS}/catalogue').json()['tools']
        read_tools = [t['name'] for t in tools if t['classification'] == 'read']
        response = self._create_workflow(write_tool_allowlist=read_tools[:1])
        self.assertEqual(400, response.status_code)

    def test_create_workflow_with_customer_scope_outside_the_owner_should_return_400(self):
        user = self._subject.create_dummy_user(permissions=_AI_WORKFLOWS_READ | _AI_WORKFLOWS_WRITE)
        customer = self._subject.create_dummy_customer()
        response = user.create(_AI_WORKFLOWS, _body(customer_scope=[customer]))
        self.assertEqual(400, response.status_code)

    def test_non_admin_setting_another_owner_should_return_403(self):
        user = self._subject.create_dummy_user(permissions=_AI_WORKFLOWS_READ | _AI_WORKFLOWS_WRITE)
        response = user.create(_AI_WORKFLOWS, _body(owner_id=1))
        self.assertEqual(403, response.status_code)

    def test_validate_should_return_errors_without_saving(self):
        response = self._subject.create(f'{_AI_WORKFLOWS}/validate',
                                        {'graph': {'nodes': [], 'edges': []}, 'trigger_type': 'manual',
                                         'trigger_config': {}, 'write_tool_allowlist': []})
        self.assertEqual(200, response.status_code)
        self.assertFalse(response.json()['valid'])

    def test_validate_with_read_permission_only_should_return_403(self):
        user = self._subject.create_dummy_user(permissions=_AI_WORKFLOWS_READ)
        response = user.create(f'{_AI_WORKFLOWS}/validate', _body())
        self.assertEqual(403, response.status_code)

    def test_create_workflow_over_2_mib_should_return_413(self):
        response = self._create_workflow(description='x' * (2 * 1024 * 1024 + 1))
        self.assertEqual(413, response.status_code)

    def test_create_workflow_with_a_description_over_10000_characters_should_return_400(self):
        response = self._create_workflow(description='x' * 10_001)
        self.assertEqual(400, response.status_code)

    def test_list_workflows_should_not_return_the_graph(self):
        self._create_workflow()
        workflows = self._subject.get(_AI_WORKFLOWS).json()
        self.assertNotIn('graph', workflows[0])

    # ---- Permissions ---------------------------------------------------

    def test_list_workflows_without_permission_should_return_403(self):
        user = self._subject.create_dummy_user()
        response = user.get(_AI_WORKFLOWS)
        self.assertEqual(403, response.status_code)

    def test_create_workflow_without_permission_should_return_403(self):
        user = self._subject.create_dummy_user()
        response = user.create(_AI_WORKFLOWS, _body())
        self.assertEqual(403, response.status_code)

    def test_create_workflow_with_read_permission_only_should_return_403(self):
        user = self._subject.create_dummy_user(permissions=_AI_WORKFLOWS_READ)
        response = user.create(_AI_WORKFLOWS, _body())
        self.assertEqual(403, response.status_code)

    def test_get_workflow_of_another_user_should_return_404(self):
        identifier = self._create_workflow().json()['id']
        user = self._subject.create_dummy_user(permissions=_AI_WORKFLOWS_READ | _AI_WORKFLOWS_WRITE)
        response = user.get(f'{_AI_WORKFLOWS}/{identifier}')
        self.assertEqual(404, response.status_code)

    def test_run_workflow_of_another_user_should_return_404(self):
        identifier = self._create_workflow().json()['id']
        alert_identifier = self._create_alert()
        user = self._subject.create_dummy_user(permissions=_AI_WORKFLOWS_READ | _AI_WORKFLOWS_WRITE)
        response = user.create(f'{_AI_WORKFLOWS}/{identifier}/run',
                               {'entity_type': 'alert', 'entity_id': alert_identifier, 'dry_run': True})
        self.assertEqual(404, response.status_code)

    def test_delete_user_owning_a_workflow_should_return_400(self):
        user = self._subject.create_dummy_user(permissions=_AI_WORKFLOWS_READ | _AI_WORKFLOWS_WRITE)
        self.assertEqual(201, user.create(_AI_WORKFLOWS, _body()).status_code)
        self._subject.update(f'/api/v2/manage/users/{user.get_identifier()}', {'user_active': False})
        response = self._subject.delete(f'/api/v2/manage/users/{user.get_identifier()}')
        self.assertEqual(400, response.status_code)

    def test_runs_without_permission_should_return_403(self):
        user = self._subject.create_dummy_user()
        response = user.get(f'{_AI_WORKFLOWS}/runs')
        self.assertEqual(403, response.status_code)

    # ---- Runs ----------------------------------------------------------

    def test_administrator_run_should_act_as_the_owner_and_be_triggered_by_the_administrator(self):
        owner = self._subject.create_dummy_user(permissions=_AI_WORKFLOWS_READ | _AI_WORKFLOWS_WRITE)
        identifier = owner.create(_AI_WORKFLOWS, _body()).json()['id']
        alert_identifier = self._create_alert()
        run = self._subject.create(f'{_AI_WORKFLOWS}/{identifier}/run',
                                   {'entity_type': 'alert', 'entity_id': alert_identifier, 'dry_run': True}).json()
        self.assertEqual(owner.get_identifier(), run['run_as']['id'])
        self.assertNotEqual(owner.get_identifier(), run['triggered_by']['id'])

    def test_manual_dry_run_on_an_alert_should_reach_a_final_or_waiting_state(self):
        identifier = self._create_workflow().json()['id']
        alert_identifier = self._create_alert()
        response = self._subject.create(f'{_AI_WORKFLOWS}/{identifier}/run',
                                        {'entity_type': 'alert', 'entity_id': alert_identifier, 'dry_run': True})
        self.assertEqual(201, response.status_code)
        run = self._wait_for_run(response.json()['uuid'])
        self.assertIn(run['status'], _TERMINAL_OR_WAITING)
        self.assertTrue(run['is_dry_run'])

    def test_manual_run_on_a_non_allowed_entity_type_should_return_400(self):
        identifier = self._create_workflow().json()['id']
        response = self._subject.create(f'{_AI_WORKFLOWS}/{identifier}/run',
                                        {'entity_type': 'case', 'entity_id': 1, 'dry_run': True})
        self.assertEqual(400, response.status_code)

    def test_manual_run_on_an_unknown_alert_should_return_404(self):
        identifier = self._create_workflow().json()['id']
        response = self._subject.create(f'{_AI_WORKFLOWS}/{identifier}/run',
                                        {'entity_type': 'alert', 'entity_id': _IDENTIFIER_FOR_NONEXISTENT_OBJECT,
                                         'dry_run': True})
        self.assertEqual(404, response.status_code)

    def test_list_runs_should_return_the_pagination_envelope(self):
        response = self._subject.get(f'{_AI_WORKFLOWS}/runs', {'page': 1, 'per_page': 5})
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertTrue({'data', 'total', 'page', 'per_page'} <= set(body))

    def test_get_unknown_run_should_return_404(self):
        response = self._subject.get(f'{_AI_WORKFLOWS}/runs/{uuid4()}')
        self.assertEqual(404, response.status_code)

    # ---- Public endpoints ----------------------------------------------

    def test_callback_with_bad_token_should_return_401(self):
        response = _post_raw(f'{_AI_WORKFLOWS}/callbacks/{uuid4()}', b'{}',
                             {'Authorization': 'Bearer nope', 'Content-Type': 'application/json'})
        self.assertEqual(401, response.status_code)
        self.assertEqual({'message': 'Unauthorized'}, response.json())

    def test_callback_without_token_should_return_401(self):
        response = _post_raw(f'{_AI_WORKFLOWS}/callbacks/not-a-uuid', b'{}', {})
        self.assertEqual(401, response.status_code)

    def test_hook_with_bad_token_should_return_401_and_be_logged(self):
        workflow = self._create_workflow(trigger_type='webhook', trigger_config={}).json()
        token = self._subject.create(f"{_AI_WORKFLOWS}/{workflow['id']}/inbound-token", {}).json()['token']
        self.assertTrue(token)
        response = _post_raw(f"{_AI_WORKFLOWS}/hooks/{workflow['uuid']}", b'{}',
                             {'Authorization': 'Bearer wrong-token'})
        self.assertEqual(401, response.status_code)
        events = self._subject.get(f'{_AI_WORKFLOWS}/inbound-events', {'workflow_id': workflow['id']}).json()
        self.assertEqual('rejected', events[0]['status'])

    def test_hook_with_the_inbound_token_should_return_202(self):
        workflow = self._create_workflow(trigger_type='webhook', trigger_config={}).json()
        token = self._subject.create(f"{_AI_WORKFLOWS}/{workflow['id']}/inbound-token", {}).json()['token']
        response = _post_raw(f"{_AI_WORKFLOWS}/hooks/{workflow['uuid']}", b'{"hello": "world"}',
                             {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'})
        self.assertEqual(202, response.status_code)
        self.assertIn('run_uuid', response.json())
        self.assertEqual('accepted', response.json()['status'])

    def test_inbound_token_rotation_should_return_a_signing_secret(self):
        workflow = self._create_workflow(trigger_type='webhook', trigger_config={}).json()
        rotated = self._subject.create(f"{_AI_WORKFLOWS}/{workflow['id']}/inbound-token", {}).json()
        self.assertTrue(rotated['signing_secret'])
        self.assertNotEqual(rotated['token'], rotated['signing_secret'])
        stored = self._subject.get(f"{_AI_WORKFLOWS}/{workflow['id']}").json()
        self.assertTrue(stored['has_signing_secret'])
        self.assertNotIn(rotated['signing_secret'], str(stored))

    def test_hook_signed_with_the_signing_secret_should_return_202(self):
        workflow = self._create_workflow(trigger_type='webhook', trigger_config={'require_signature': True}).json()
        rotated = self._subject.create(f"{_AI_WORKFLOWS}/{workflow['id']}/inbound-token", {}).json()
        raw = b'{"hello": "world"}'
        response = _post_raw(f"{_AI_WORKFLOWS}/hooks/{workflow['uuid']}", raw,
                             _signed_headers(rotated['token'], rotated['signing_secret'], raw))
        self.assertEqual(202, response.status_code)

    def test_hook_signed_with_the_token_should_return_401(self):
        workflow = self._create_workflow(trigger_type='webhook', trigger_config={'require_signature': True}).json()
        rotated = self._subject.create(f"{_AI_WORKFLOWS}/{workflow['id']}/inbound-token", {}).json()
        raw = b'{"hello": "world"}'
        response = _post_raw(f"{_AI_WORKFLOWS}/hooks/{workflow['uuid']}", raw,
                             _signed_headers(rotated['token'], rotated['token'], raw))
        self.assertEqual(401, response.status_code)

    def test_replayed_signature_should_return_401(self):
        workflow = self._create_workflow(trigger_type='webhook', trigger_config={'require_signature': True}).json()
        rotated = self._subject.create(f"{_AI_WORKFLOWS}/{workflow['id']}/inbound-token", {}).json()
        raw = b'{"hello": "world"}'
        headers = _signed_headers(rotated['token'], rotated['signing_secret'], raw)
        self.assertEqual(202, _post_raw(f"{_AI_WORKFLOWS}/hooks/{workflow['uuid']}", raw, headers).status_code)
        self.assertEqual(401, _post_raw(f"{_AI_WORKFLOWS}/hooks/{workflow['uuid']}", raw, headers).status_code)

    def test_signing_secret_rotation_should_keep_the_token(self):
        workflow = self._create_workflow(trigger_type='webhook', trigger_config={}).json()
        rotated = self._subject.create(f"{_AI_WORKFLOWS}/{workflow['id']}/inbound-token", {}).json()
        response = self._subject.create(f"{_AI_WORKFLOWS}/{workflow['id']}/signing-secret", {})
        self.assertEqual(200, response.status_code)
        secret = response.json()['signing_secret']
        self.assertNotEqual(rotated['signing_secret'], secret)
        raw = b'{}'
        hook = _post_raw(f"{_AI_WORKFLOWS}/hooks/{workflow['uuid']}", raw,
                         _signed_headers(rotated['token'], secret, raw))
        self.assertEqual(202, hook.status_code)

    def test_hook_with_non_standard_json_should_return_400(self):
        workflow = self._create_workflow(trigger_type='webhook', trigger_config={}).json()
        token = self._subject.create(f"{_AI_WORKFLOWS}/{workflow['id']}/inbound-token", {}).json()['token']
        response = _post_raw(f"{_AI_WORKFLOWS}/hooks/{workflow['uuid']}", b'{"value": NaN}',
                             {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'})
        self.assertEqual(400, response.status_code)

    def test_hook_on_an_unknown_workflow_should_not_be_logged(self):
        before = len(self._subject.get(f'{_AI_WORKFLOWS}/inbound-events').json())
        for target in (str(uuid4()), 'not-a-uuid'):
            response = _post_raw(f'{_AI_WORKFLOWS}/hooks/{target}', b'{}', {'Authorization': 'Bearer nope'})
            self.assertEqual(401, response.status_code)
        after = len(self._subject.get(f'{_AI_WORKFLOWS}/inbound-events').json())
        self.assertEqual(before, after)

    def test_inbound_token_on_a_manual_workflow_should_return_400(self):
        identifier = self._create_workflow().json()['id']
        response = self._subject.create(f'{_AI_WORKFLOWS}/{identifier}/inbound-token', {})
        self.assertEqual(400, response.status_code)

    # ---- Suggestions ---------------------------------------------------

    def test_list_suggestions_should_return_200_for_any_user(self):
        user = self._subject.create_dummy_user()
        response = user.get(_AI_SUGGESTIONS)
        self.assertEqual(200, response.status_code)

    def test_counts_should_return_zeros(self):
        alert_identifier = self._create_alert()
        response = self._subject.get(f'{_AI_SUGGESTIONS}/counts',
                                     {'entity_type': 'alert', 'entity_ids': f'{alert_identifier}'})
        self.assertEqual(200, response.status_code)
        self.assertEqual({str(alert_identifier): 0}, response.json())

    def test_get_unknown_suggestion_should_return_404(self):
        response = self._subject.get(f'{_AI_SUGGESTIONS}/{_IDENTIFIER_FOR_NONEXISTENT_OBJECT}')
        self.assertEqual(404, response.status_code)
