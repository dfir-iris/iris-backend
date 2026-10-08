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

"""Security fixes of the AI workflow REST surface: who a manual run or
re-run acts as and is bounded by, run visibility, exact paging totals,
404 for invisible workflows, definition caps, delete ordering, list
and run-detail size caps, resolver-only data, signing secret rotation
and the blueprint body cap / error mapping."""

import io
import uuid
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock
from unittest.mock import call
from unittest.mock import patch

from app import app
from app.blueprints.rest.v2 import ai_workflows as ai_workflows_routes
from app.business.ai_workflows import AiWorkflowsDisabledError
from app.business.ai_workflows import ai_workflows_create
from app.business.ai_workflows import ai_workflows_delete
from app.business.ai_workflows import ai_workflows_get
from app.business.ai_workflows import ai_workflows_list
from app.business.ai_workflows import ai_workflows_list_runs
from app.business.ai_workflows import ai_workflows_rerun
from app.business.ai_workflows import ai_workflows_rotate_inbound_token
from app.business.ai_workflows import ai_workflows_rotate_signing_secret
from app.business.ai_workflows import ai_workflows_run_manual
from app.business.ai_workflows import ai_workflows_serialize
from app.business.ai_workflows import ai_workflows_suggestion_public
from app.business.ai_workflows import ai_workflows_update
from app.business.ai_workflows import ai_workflows_user_can_see_run
from app.business.ai_workflows import _cap_tool_calls
from app.business.ai_workflows import _tool_call_public
from app.business.ai_workflows import _wait_public
from app.models.ai_workflows import AiWorkflow
from app.models.ai_workflows import TRIGGER_MANUAL
from app.models.ai_workflows import TRIGGER_WEBHOOK
from app.models.authorization import Permissions
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError

_BUSINESS = 'app.business.ai_workflows'
_OWNER = 10
_OTHER = 11
_ADMIN = 1
_TOOLS = [
    {'name': 'get_alert', 'classification': 'read'},
    {'name': 'update_alert', 'classification': 'write'},
] + [{'name': f'write_{i}', 'classification': 'write'} for i in range(250)]


def _body(**overrides):
    body = {
        'name': 'Triage',
        'trigger_type': TRIGGER_MANUAL,
        'trigger_config': {'entity_types': ['alert']},
        'graph': {'nodes': [], 'edges': []},
    }
    body.update(overrides)
    return body


def _stored_workflow(**overrides):
    values = {
        'name': 'Stored', 'description': None, 'is_active': True, 'trigger_type': TRIGGER_MANUAL,
        'trigger_config': {'entity_types': ['alert']}, 'customer_scope': [], 'graph': {'nodes': [{'id': 'n'}], 'edges': []},
        'owner_id': _OWNER, 'write_tool_allowlist': [], 'max_runs_per_hour': 60, 'token_budget_per_run': 50000,
        'suggestion_audience': 'entity', 'version': 1,
    }
    values.update(overrides)
    workflow = AiWorkflow(**values)
    workflow.id = 3
    workflow.uuid = uuid.uuid4()
    return workflow


def _run(**overrides):
    values = {
        'id': 1, 'uuid': uuid.uuid4(), 'workflow': SimpleNamespace(owner_id=_OWNER), 'workflow_id': 3,
        'workflow_name': 'Triage', 'definition_snapshot': {'owner_id': _OWNER}, 'owner_id': None,
        'run_as_user_id': _OWNER, 'triggered_by_user_id': None, 'entity_type': 'alert', 'entity_id': 4,
        'trigger_type': TRIGGER_MANUAL, 'sub_entity': None, 'trigger_payload': {'a': 1}, 'is_dry_run': False,
        'chain_depth': 0, 'parent_run_id': None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class _WorkflowsTestCase(TestCase):

    def setUp(self):
        self.users = {i: SimpleNamespace(id=i, active=True) for i in (_OWNER, _OTHER, _ADMIN)}
        self.accessible_customers = {_OWNER: [1, 2], _OTHER: [3], _ADMIN: None}
        self.added = []
        self.can_access = MagicMock(return_value=True)
        self.start_run = MagicMock(return_value=SimpleNamespace(uuid=uuid.uuid4()))
        self.workflows = {}
        self.runs = {}
        patchers = [
            patch(f'{_BUSINESS}.ai_workflows_db_get_user', side_effect=lambda i: self.users.get(i)),
            patch(f'{_BUSINESS}.ai_workflows_db_get', side_effect=lambda i: self.workflows.get(i)),
            patch(f'{_BUSINESS}.ai_workflows_db_get_run_by_uuid', side_effect=lambda u: self.runs.get(u)),
            patch(f'{_BUSINESS}.ai_workflows_db_add', side_effect=self.added.append),
            patch(f'{_BUSINESS}.ai_workflows_db_commit'),
            patch(f'{_BUSINESS}.ai_workflows_db_run_counts', return_value={}),
            patch(f'{_BUSINESS}.ai_workflows_db_user_summary', return_value={}),
            patch(f'{_BUSINESS}.ai_workflows_db_entity_exists', return_value=True),
            patch(f'{_BUSINESS}.ai_workflows_db_entity_customer', return_value=1),
            patch(f'{_BUSINESS}.ac_get_effective_permissions_of_user', return_value=0),
            patch(f'{_BUSINESS}.access_controls_user_accessible_customers',
                  side_effect=lambda user, _permissions: self.accessible_customers.get(user.id)),
            patch(f'{_BUSINESS}.ai_workflows_tools_catalogue', return_value=_TOOLS),
            patch(f'{_BUSINESS}.ai_workflows_graph_validate_trigger_config', return_value=[]),
            patch(f'{_BUSINESS}.ai_workflows_graph_validate', return_value=[]),
            patch(f'{_BUSINESS}.ai_workflows_entities_user_can_access', self.can_access),
            patch(f'{_BUSINESS}.ai_workflows_engine_start_run', self.start_run),
            patch(f'{_BUSINESS}.ai_workflows_run_summary', side_effect=lambda r, *_args: {'uuid': str(r.uuid)}),
            patch(f'{_BUSINESS}.track_activity'),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def _errors(self, body):
        with self.assertRaises(BusinessProcessingError) as context:
            ai_workflows_create(body, _OWNER, False)
        return context.exception.get_data()['errors']


class TestsManualRunIdentity(_WorkflowsTestCase):
    """HIGH: run_as = owner, triggered_by = clicker, clicker's scope."""

    def setUp(self):
        super().setUp()
        self.workflows[3] = _stored_workflow()

    def test_admin_run_of_another_owners_workflow_acts_as_the_owner(self):
        ai_workflows_run_manual(3, {'entity_type': 'alert', 'entity_id': 4}, _ADMIN, True, scope_mask=None)
        kwargs = self.start_run.call_args.kwargs
        self.assertEqual(_OWNER, kwargs['run_as_user_id'])
        self.assertEqual(_ADMIN, kwargs['triggered_by_user_id'])
        self.assertIsNone(kwargs['scope_mask'])

    def test_dry_run_should_pass_the_api_key_scope(self):
        mask = Permissions.alerts_read.value | Permissions.ai_workflows_write.value
        ai_workflows_run_manual(3, {'entity_type': 'alert', 'entity_id': 4, 'dry_run': True}, _ADMIN, True,
                                scope_mask=mask)
        kwargs = self.start_run.call_args.kwargs
        self.assertEqual(mask, kwargs['scope_mask'])
        self.assertTrue(kwargs['dry_run'])

    def test_clicker_must_access_the_entity_even_as_admin(self):
        self.can_access.return_value = False
        with self.assertRaises(ObjectNotFoundError):
            ai_workflows_run_manual(3, {'entity_type': 'alert', 'entity_id': 4}, _ADMIN, True)
        self.assertEqual(_ADMIN, self.can_access.call_args.args[0])
        self.start_run.assert_not_called()

    def test_key_scope_without_the_entity_read_bit_should_be_not_found(self):
        with self.assertRaises(ObjectNotFoundError):
            ai_workflows_run_manual(3, {'entity_type': 'alert', 'entity_id': 4}, _OWNER, False,
                                    scope_mask=Permissions.ai_workflows_write.value)
        self.start_run.assert_not_called()

    def test_non_owner_should_not_find_the_workflow(self):
        with self.assertRaises(ObjectNotFoundError):
            ai_workflows_run_manual(3, {'entity_type': 'alert', 'entity_id': 4}, _OTHER, False)

    def test_disabled_feature_should_raise_the_disabled_error(self):
        with patch.dict(app.config, {'AI_WORKFLOWS_ENABLED': False}):
            with self.assertRaises(AiWorkflowsDisabledError):
                ai_workflows_run_manual(3, {'entity_type': 'alert', 'entity_id': 4}, _OWNER, False)

    def test_unrestricted_manual_trigger_should_accept_any_entity_type(self):
        self.workflows[3] = _stored_workflow(trigger_config={'entity_types': []})
        ai_workflows_run_manual(3, {'entity_type': 'case', 'entity_id': 4}, _OWNER, False)
        self.assertEqual(('case', 4), (self.start_run.call_args.kwargs['entity_type'],
                                       self.start_run.call_args.kwargs['entity_id']))

    def test_unrestricted_manual_trigger_should_accept_no_entity(self):
        self.workflows[3] = _stored_workflow(trigger_config={})
        ai_workflows_run_manual(3, {}, _OWNER, False)
        self.assertIsNone(self.start_run.call_args.kwargs['entity_type'])

    def test_restricted_manual_trigger_should_refuse_other_types_and_no_entity(self):
        for body in ({'entity_type': 'case', 'entity_id': 4}, {}):
            with self.assertRaises(BusinessProcessingError):
                ai_workflows_run_manual(3, body, _OWNER, False)
        self.start_run.assert_not_called()


class TestsRerunIdentity(_WorkflowsTestCase):

    def setUp(self):
        super().setUp()
        self.workflows[3] = _stored_workflow()
        self.run = _run(owner_id=_OWNER)
        self.runs[self.run.uuid] = self.run

    def test_admin_rerun_acts_as_the_owner_triggered_by_the_admin_with_scope(self):
        mask = Permissions.alerts_read.value
        ai_workflows_rerun(str(self.run.uuid), _ADMIN, True, scope_mask=mask)
        kwargs = self.start_run.call_args.kwargs
        self.assertEqual(_OWNER, kwargs['run_as_user_id'])
        self.assertEqual(_ADMIN, kwargs['triggered_by_user_id'])
        self.assertEqual(mask, kwargs['scope_mask'])
        self.assertEqual({'a': 1}, kwargs['payload'])

    def test_rerun_should_need_entity_access_of_the_clicker(self):
        self.can_access.side_effect = lambda user_id, *_args: user_id != _ADMIN
        with self.assertRaises(ObjectNotFoundError):
            ai_workflows_rerun(str(self.run.uuid), _ADMIN, True)
        self.start_run.assert_not_called()

    def test_triggering_user_who_does_not_own_the_workflow_cannot_rerun(self):
        self.run.triggered_by_user_id = _OTHER
        with self.assertRaises(ObjectNotFoundError):
            ai_workflows_rerun(str(self.run.uuid), _OTHER, False)
        self.start_run.assert_not_called()


class TestsRunVisibilityOwner(TestCase):
    """LOW: visibility uses the run's owner at start, not today's owner."""

    def setUp(self):
        patcher = patch(f'{_BUSINESS}.ai_workflows_entities_user_can_access', return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_owner_at_run_start_should_see_the_run_after_a_transfer(self):
        run = _run(owner_id=_OWNER, workflow=SimpleNamespace(owner_id=_OTHER), run_as_user_id=99)
        self.assertTrue(ai_workflows_user_can_see_run(_OWNER, False, run))

    def test_new_owner_should_not_see_runs_of_the_previous_owner(self):
        run = _run(owner_id=_OWNER, workflow=SimpleNamespace(owner_id=_OTHER), run_as_user_id=_OWNER)
        self.assertFalse(ai_workflows_user_can_see_run(_OTHER, False, run))

    def test_scope_without_entity_read_should_hide_the_run(self):
        run = _run(owner_id=_OWNER)
        self.assertFalse(ai_workflows_user_can_see_run(_OWNER, False, run, scope_mask=0))


class TestsRunsListTotal(TestCase):

    def test_total_and_pages_should_count_only_visible_runs(self):
        light = [SimpleNamespace(id=i, entity_type='alert', entity_id=i) for i in range(1, 8)]
        rows = {i: _run(id=i, entity_id=i) for i in range(1, 8)}
        with patch(f'{_BUSINESS}.ai_workflows_business_db_involved_runs', return_value=light), \
                patch(f'{_BUSINESS}.ai_workflows_business_db_runs_by_ids',
                      side_effect=lambda ids: [rows[i] for i in ids]) as by_ids, \
                patch(f'{_BUSINESS}.ai_workflows_entities_user_can_access',
                      side_effect=lambda _u, _t, entity_id: entity_id % 2 == 1), \
                patch(f'{_BUSINESS}.ai_workflows_db_user_summary', return_value={}), \
                patch(f'{_BUSINESS}.ai_workflows_run_summary', side_effect=lambda r, *_args: r.id):
            result = ai_workflows_list_runs(_OWNER, False, page=2, per_page=2)
        self.assertEqual(4, result['total'])
        self.assertEqual(2, result['last_page'])
        self.assertIsNone(result['next_page'])
        self.assertEqual([5, 7], result['data'])
        by_ids.assert_called_once_with([5, 7])

    def test_api_key_scope_should_filter_the_list(self):
        light = [SimpleNamespace(id=1, entity_type='alert', entity_id=1)]
        with patch(f'{_BUSINESS}.ai_workflows_business_db_involved_runs', return_value=light), \
                patch(f'{_BUSINESS}.ai_workflows_business_db_runs_by_ids', return_value=[]), \
                patch(f'{_BUSINESS}.ai_workflows_entities_user_can_access', return_value=True), \
                patch(f'{_BUSINESS}.ai_workflows_db_user_summary', return_value={}):
            result = ai_workflows_list_runs(_OWNER, False, scope_mask=0)
        self.assertEqual(0, result['total'])


class TestsNotFoundForNonOwners(_WorkflowsTestCase):

    def setUp(self):
        super().setUp()
        self.workflows[3] = _stored_workflow(trigger_type=TRIGGER_WEBHOOK, trigger_config={})

    def test_get_update_delete_and_rotate_should_be_not_found(self):
        operations = [
            lambda: ai_workflows_get(3, _OTHER, False),
            lambda: ai_workflows_update(3, {'name': 'x'}, _OTHER, False),
            lambda: ai_workflows_delete(3, _OTHER, False),
            lambda: ai_workflows_rotate_inbound_token(3, _OTHER, False),
            lambda: ai_workflows_rotate_signing_secret(3, _OTHER, False),
        ]
        for operation in operations:
            with self.assertRaises(ObjectNotFoundError):
                operation()


class TestsDefinitionCaps(_WorkflowsTestCase):

    def test_description_over_10k_should_be_rejected(self):
        errors = self._errors(_body(description='x' * 10_001))
        self.assertIn('description', [e['field'] for e in errors])

    def test_description_of_10k_should_be_accepted(self):
        data = ai_workflows_create(_body(description='x' * 10_000), _OWNER, False)
        self.assertEqual(10_000, len(data['description']))

    def test_allowlist_should_be_deduplicated(self):
        data = ai_workflows_create(_body(write_tool_allowlist=['update_alert', 'update_alert']), _OWNER, False)
        self.assertEqual(['update_alert'], data['write_tool_allowlist'])

    def test_allowlist_over_200_tools_should_be_rejected(self):
        errors = self._errors(_body(write_tool_allowlist=[f'write_{i}' for i in range(201)]))
        self.assertIn('write_tool_allowlist', [e['field'] for e in errors])

    def test_customer_scope_over_1000_ids_should_be_rejected(self):
        errors = self._errors(_body(customer_scope=list(range(1, 1002))))
        self.assertIn('customer_scope', [e['field'] for e in errors])

    def test_allowlist_should_refuse_read_tools(self):
        errors = self._errors(_body(write_tool_allowlist=['get_alert']))
        self.assertTrue(any('read tool' in e['message'] for e in errors))

    def test_token_budget_should_be_at_least_1(self):
        errors = self._errors(_body(token_budget_per_run=0))
        self.assertIn('token_budget_per_run', [e['field'] for e in errors])
        data = ai_workflows_create(_body(token_budget_per_run=1), _OWNER, False)
        self.assertEqual(1, data['token_budget_per_run'])


class TestsDeleteOrder(_WorkflowsTestCase):
    """#15: lock, deactivate, commit, cancel the active runs, delete."""

    def test_delete_should_deactivate_and_commit_before_cancelling(self):
        workflow = self.workflows[3] = _stored_workflow()
        runs = [SimpleNamespace(id=7), SimpleNamespace(id=8)]
        order = MagicMock()
        order.lock.side_effect = lambda _id: workflow
        order.commit.side_effect = lambda: order.active_at_commit(workflow.is_active)
        order.active.return_value = runs
        with patch(f'{_BUSINESS}.ai_workflows_db_lock_workflow', order.lock), \
                patch(f'{_BUSINESS}.ai_workflows_db_commit', order.commit), \
                patch(f'{_BUSINESS}.ai_workflows_db_active_runs_for_workflow', order.active), \
                patch(f'{_BUSINESS}.ai_workflows_engine_cancel_run', order.cancel), \
                patch(f'{_BUSINESS}.ai_workflows_db_delete', order.delete):
            ai_workflows_delete(3, _OWNER, False)
        names = [c[0] for c in order.mock_calls]
        self.assertEqual(['lock', 'commit', 'active_at_commit', 'active', 'cancel', 'cancel', 'delete'], names)
        self.assertEqual(call.active_at_commit(False), order.mock_calls[2])
        order.cancel.assert_any_call(runs[0], _OWNER, reason='Workflow deleted')

    def test_workflow_gone_under_the_lock_should_be_not_found(self):
        self.workflows[3] = _stored_workflow()
        with patch(f'{_BUSINESS}.ai_workflows_db_lock_workflow', return_value=None), \
                patch(f'{_BUSINESS}.ai_workflows_db_delete') as delete:
            with self.assertRaises(ObjectNotFoundError):
                ai_workflows_delete(3, _OWNER, False)
        delete.assert_not_called()


class TestsListAndSerialization(_WorkflowsTestCase):

    def test_list_should_not_carry_the_graph(self):
        with patch(f'{_BUSINESS}.ai_workflows_db_list', return_value=[_stored_workflow()]):
            data = ai_workflows_list(_OWNER, False)
        self.assertNotIn('graph', data[0])

    def test_detail_should_carry_the_graph_and_skip_counters(self):
        workflow = _stored_workflow(skipped_count=3, last_skip_reason='rate limit', inbound_signing_secret='enc:x')
        data = ai_workflows_serialize(workflow)
        self.assertEqual({'nodes': [{'id': 'n'}], 'edges': []}, data['graph'])
        self.assertEqual(3, data['skipped_count'])
        self.assertEqual('rate limit', data['last_skip_reason'])
        self.assertTrue(data['has_signing_secret'])
        self.assertNotIn('inbound_signing_secret', data)


def _tool_call(**overrides):
    values = {
        'id': 1, 'step_id': 1, 'suggestion_id': None, 'tool_name': 'update_alert', 'arguments': {},
        'result': {'ok': True}, 'error': None, 'classification': 'write', 'execution_mode': 'auto',
        'acting_user_id': _OWNER, 'duration_ms': 1, 'created_at': None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class TestsRunDetailCaps(TestCase):

    def test_large_tool_result_should_be_truncated_at_16k(self):
        data = _tool_call_public(_tool_call(result={'blob': 'x' * 40_000}), {}, _OWNER)
        self.assertTrue(data['result_truncated'])
        self.assertEqual(16 * 1024, len(data['result']))

    def test_small_tool_result_should_be_kept(self):
        data = _tool_call_public(_tool_call(), {}, _OWNER)
        self.assertEqual({'ok': True}, data['result'])
        self.assertFalse(data['result_truncated'])
        self.assertFalse(data['result_hidden'])

    def test_accepted_result_should_be_hidden_from_everyone_but_the_resolver(self):
        accepted = _tool_call(execution_mode='accepted_by_user', acting_user_id=_OTHER)
        self.assertTrue(_tool_call_public(accepted, {}, _OWNER)['result_hidden'])
        self.assertIsNone(_tool_call_public(accepted, {}, _OWNER)['result'])
        self.assertEqual({'ok': True}, _tool_call_public(accepted, {}, _OTHER)['result'])

    def test_tool_calls_should_be_capped_at_200_per_step(self):
        calls = [_tool_call(id=i, step_id=1) for i in range(250)] + [_tool_call(id=900, step_id=2)]
        kept, omitted = _cap_tool_calls(calls)
        self.assertEqual(201, len(kept))
        self.assertEqual(50, omitted)

    def test_wait_payload_should_be_hidden_from_everyone_but_the_resolver(self):
        wait = SimpleNamespace(id=1, uuid=uuid.uuid4(), node_id='n', kind='info_request', status='resolved',
                               expires_at=None, resolved_payload={'answer': 'secret'}, resolved_by_id=_OTHER,
                               resolved_at=None, source_ip=None, payload_sha256=None, suggestion_id=None,
                               created_at=None)
        hidden = _wait_public(wait, {}, _OWNER)
        self.assertIsNone(hidden['resolved_payload'])
        self.assertTrue(hidden['resolved_payload_hidden'])
        self.assertEqual({'answer': 'secret'}, _wait_public(wait, {}, _OTHER)['resolved_payload'])

    def test_suggestion_resolution_should_be_hidden_from_others(self):
        suggestion = SimpleNamespace(resolved_by_id=_OTHER)
        serialized = {'result': {'ok': True}, 'answer': 'yes'}
        with patch(f'{_BUSINESS}.ai_workflows_suggestions_serialize', side_effect=lambda _s: dict(serialized)):
            hidden = ai_workflows_suggestion_public(suggestion, _OWNER)
            shown = ai_workflows_suggestion_public(suggestion, _OTHER)
        self.assertEqual((None, None, True), (hidden['result'], hidden['answer'], hidden['resolution_hidden']))
        self.assertEqual(({'ok': True}, 'yes', False), (shown['result'], shown['answer'], shown['resolution_hidden']))


class TestsSigningSecret(_WorkflowsTestCase):

    def setUp(self):
        super().setUp()
        self.workflow = self.workflows[3] = _stored_workflow(trigger_type=TRIGGER_WEBHOOK, trigger_config={})
        tokens = iter(['token-1', 'secret-1', 'secret-2'])
        patchers = [
            patch(f'{_BUSINESS}.ai_workflows_engine_new_token', side_effect=lambda: next(tokens)),
            patch(f'{_BUSINESS}.ai_workflows_engine_hash_token', side_effect=lambda t: f'hash:{t}'),
            patch(f'{_BUSINESS}.ai_workflows_engine_inbound_url', return_value='https://iris/hook'),
            patch(f'{_BUSINESS}.encrypt_secret', side_effect=lambda s: f'enc:{s}'),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_token_rotation_should_return_a_separate_signing_secret(self):
        data = ai_workflows_rotate_inbound_token(3, _OWNER, False)
        self.assertEqual({'token': 'token-1', 'signing_secret': 'secret-1', 'url': 'https://iris/hook'}, data)
        self.assertEqual('hash:token-1', self.workflow.inbound_token_hash)
        self.assertEqual('enc:secret-1', self.workflow.inbound_signing_secret)

    def test_signing_secret_rotation_should_keep_the_token(self):
        self.workflow.inbound_token_hash = 'hash:old'
        data = ai_workflows_rotate_signing_secret(3, _OWNER, False)
        self.assertEqual({'signing_secret': 'token-1'}, data)
        self.assertEqual('hash:old', self.workflow.inbound_token_hash)
        self.assertEqual('enc:token-1', self.workflow.inbound_signing_secret)

    def test_non_webhook_workflow_has_no_signing_secret(self):
        self.workflow.trigger_type = TRIGGER_MANUAL
        with self.assertRaises(BusinessProcessingError):
            ai_workflows_rotate_signing_secret(3, _OWNER, False)

    def test_leaving_webhook_should_drop_the_signing_secret(self):
        self.workflow.inbound_signing_secret = 'enc:x'
        ai_workflows_update(3, {'trigger_type': TRIGGER_MANUAL, 'trigger_config': {'entity_types': ['alert']}},
                            _OWNER, False)
        self.assertIsNone(self.workflow.inbound_signing_secret)


class TestsBlueprintHelpers(TestCase):

    def test_definition_over_2mb_should_be_413(self):
        raw = b'{"a": "' + b'x' * (3 * 1024 * 1024) + b'"}'
        with app.test_request_context('/', method='POST', data=raw, content_type='application/json'):
            body, refused = ai_workflows_routes._definition_body()
        self.assertIsNone(body)
        self.assertEqual(413, refused.status_code)

    def test_chunked_definition_over_2mb_should_be_413(self):
        raw = b'{"a": "' + b'x' * (2 * 1024 * 1024) + b'"}'
        with app.test_request_context('/', method='POST', input_stream=io.BytesIO(raw),
                                      content_type='application/json'):
            body, refused = ai_workflows_routes._definition_body()
        self.assertIsNone(body)
        self.assertEqual(413, refused.status_code)

    def test_small_definition_should_be_parsed(self):
        with app.test_request_context('/', method='POST', data=b'{"name": "x"}', content_type='application/json'):
            body, refused = ai_workflows_routes._definition_body()
        self.assertIsNone(refused)
        self.assertEqual({'name': 'x'}, body)

    def test_invalid_json_definition_should_be_an_empty_body(self):
        with app.test_request_context('/', method='POST', data=b'{nope', content_type='application/json'):
            body, refused = ai_workflows_routes._definition_body()
        self.assertIsNone(refused)
        self.assertEqual({}, body)

    def test_disabled_feature_should_map_to_403_and_not_found_to_404(self):
        def _disabled():
            raise AiWorkflowsDisabledError()

        def _missing():
            raise ObjectNotFoundError()

        with app.test_request_context('/'):
            self.assertEqual(403, ai_workflows_routes._handle(_disabled).status_code)
            self.assertEqual(404, ai_workflows_routes._handle(_missing).status_code)
