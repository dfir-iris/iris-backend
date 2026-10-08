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

"""Workflow validation (owner, customer scope, allowlist), versioning,
run visibility and manual-run entity checks. The DB helpers and the
engine are patched; workflows are transient model instances."""

import uuid
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock
from unittest.mock import patch

from app import app
from app.business.ai_workflows import AiWorkflowsForbiddenError
from app.business.ai_workflows import ai_workflows_catalogue
from app.business.ai_workflows import ai_workflows_create
from app.business.ai_workflows import ai_workflows_export_run
from app.business.ai_workflows import ai_workflows_get_run
from app.business.ai_workflows import ai_workflows_list_runs
from app.business.ai_workflows import ai_workflows_run_manual
from app.business.ai_workflows import ai_workflows_update
from app.business.ai_workflows import ai_workflows_user_can_see_run
from app.business.ai_workflows import ai_workflows_validate_definition
from app.models.ai_workflows import AiWorkflow
from app.models.ai_workflows import AiWorkflowVersion
from app.models.ai_workflows import TRIGGER_EVENT
from app.models.ai_workflows import TRIGGER_MANUAL
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError

_BUSINESS = 'app.business.ai_workflows'
_OWNER = 10
_OTHER = 11
_ADMIN = 1
_TOOLS = [
    {'name': 'get_alert', 'classification': 'read'},
    {'name': 'update_alert', 'classification': 'write'},
]


def _body(**overrides):
    body = {
        'name': 'Triage',
        'trigger_type': TRIGGER_MANUAL,
        'trigger_config': {'entity_types': ['alert']},
        'graph': {'nodes': [], 'edges': []},
    }
    body.update(overrides)
    return body


def _user(user_id, active=True):
    return SimpleNamespace(id=user_id, active=active)


def _stored_workflow(**overrides):
    values = {
        'name': 'Stored', 'description': None, 'is_active': False, 'trigger_type': TRIGGER_MANUAL,
        'trigger_config': {'entity_types': ['alert']}, 'customer_scope': [], 'graph': {'nodes': [], 'edges': []},
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
        'id': 1, 'uuid': uuid.uuid4(), 'workflow': SimpleNamespace(owner_id=_OWNER), 'workflow_id': 3, 'workflow_name': 'Triage',
        'definition_snapshot': {'owner_id': _OWNER}, 'run_as_user_id': _OWNER, 'triggered_by_user_id': None,
        'entity_type': 'alert', 'entity_id': 4,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class _WorkflowsTestCase(TestCase):

    def setUp(self):
        self.users = {_OWNER: _user(_OWNER), _OTHER: _user(_OTHER), _ADMIN: _user(_ADMIN)}
        self.accessible_customers = {_OWNER: [1, 2], _OTHER: [3], _ADMIN: None}
        self.added = []
        self.graph_errors = []
        self.can_access = MagicMock(return_value=True)
        self.start_run = MagicMock()
        self.workflows = {}
        patchers = [
            patch(f'{_BUSINESS}.ai_workflows_db_get_user', side_effect=lambda i: self.users.get(i)),
            patch(f'{_BUSINESS}.ai_workflows_db_get', side_effect=lambda i: self.workflows.get(i)),
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
            patch(f'{_BUSINESS}.ai_workflows_graph_validate', side_effect=lambda *_args, **_kwargs: self.graph_errors),
            patch(f'{_BUSINESS}.ai_workflows_entities_user_can_access', self.can_access),
            patch(f'{_BUSINESS}.ai_workflows_engine_start_run', self.start_run),
            patch(f'{_BUSINESS}.track_activity'),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def _invalid(self, body, user_id=_OWNER, is_admin=False):
        with self.assertRaises(BusinessProcessingError) as context:
            ai_workflows_create(body, user_id, is_admin)
        return context.exception.get_data()['errors']


class TestsValidation(_WorkflowsTestCase):

    def test_create_should_default_the_owner_to_the_creator_and_write_version_1(self):
        data = ai_workflows_create(_body(), _OWNER, False)
        self.assertEqual(_OWNER, data['owner_id'])
        self.assertEqual(1, data['version'])
        versions = [a for a in self.added if isinstance(a, AiWorkflowVersion)]
        self.assertEqual(1, len(versions))
        self.assertEqual(1, versions[0].version)

    def test_non_admin_cannot_set_another_owner(self):
        with self.assertRaises(AiWorkflowsForbiddenError):
            ai_workflows_create(_body(owner_id=_OTHER), _OWNER, False)

    def test_admin_can_set_another_owner(self):
        data = ai_workflows_create(_body(owner_id=_OTHER), _ADMIN, True)
        self.assertEqual(_OTHER, data['owner_id'])

    def test_inactive_owner_should_be_rejected(self):
        self.users[_OTHER] = _user(_OTHER, active=False)
        errors = self._invalid(_body(owner_id=_OTHER), _ADMIN, True)
        self.assertIn('owner_id', [e['field'] for e in errors])

    def test_customer_scope_should_be_within_the_owner_customers(self):
        errors = self._invalid(_body(customer_scope=[1, 3]))
        self.assertEqual(['customer_scope'], [e['field'] for e in errors])
        self.assertIn('[3]', errors[0]['message'])

    def test_customer_scope_is_checked_against_the_owner_not_the_admin(self):
        errors = self._invalid(_body(owner_id=_OTHER, customer_scope=[1]), _ADMIN, True)
        self.assertEqual(['customer_scope'], [e['field'] for e in errors])

    def test_customer_scope_inside_the_owner_customers_should_pass(self):
        data = ai_workflows_create(_body(customer_scope=[2, 1, 1]), _OWNER, False)
        self.assertEqual([1, 2], data['customer_scope'])

    def test_allowlist_should_hold_write_tools_only(self):
        errors = self._invalid(_body(write_tool_allowlist=['update_alert', 'get_alert', 'nope']))
        messages = ' '.join(e['message'] for e in errors)
        self.assertIn('get_alert is a read tool', messages)
        self.assertIn('Unknown tool nope', messages)

    def test_graph_errors_should_be_reported(self):
        self.graph_errors = [{'node_id': 'n1', 'field': 'prompt', 'message': 'required'}]
        errors = self._invalid(_body())
        self.assertEqual(self.graph_errors, errors)

    def test_basic_fields_should_be_validated(self):
        errors = self._invalid(_body(name=' ', trigger_type='bogus', max_runs_per_hour=0, suggestion_audience='x'))
        fields = {e['field'] for e in errors}
        self.assertTrue({'name', 'trigger_type', 'max_runs_per_hour', 'suggestion_audience'} <= fields)

    def test_validate_endpoint_should_not_raise(self):
        self.graph_errors = [{'node_id': 'n1', 'field': None, 'message': 'dangling'}]
        result = ai_workflows_validate_definition(_body())
        self.assertFalse(result['valid'])
        self.assertEqual(self.graph_errors, result['errors'])


class TestsUpdate(_WorkflowsTestCase):

    def test_definition_change_should_bump_the_version(self):
        workflow = self.workflows[3] = _stored_workflow()
        ai_workflows_update(3, {'trigger_config': {'entity_types': ['case']}, 'version_note': 'cases'}, _OWNER, False)
        self.assertEqual(2, workflow.version)
        versions = [a for a in self.added if isinstance(a, AiWorkflowVersion)]
        self.assertEqual('cases', versions[0].note)

    def test_rename_only_should_keep_the_version(self):
        workflow = self.workflows[3] = _stored_workflow()
        ai_workflows_update(3, {'name': 'Renamed'}, _OWNER, False)
        self.assertEqual(1, workflow.version)
        self.assertEqual('Renamed', workflow.name)
        self.assertEqual([], [a for a in self.added if isinstance(a, AiWorkflowVersion)])

    def test_other_users_cannot_update(self):
        self.workflows[3] = _stored_workflow()
        with self.assertRaises(ObjectNotFoundError):
            ai_workflows_update(3, {'name': 'x'}, _OTHER, False)

    def test_leaving_webhook_should_drop_the_inbound_token(self):
        workflow = self.workflows[3] = _stored_workflow(trigger_type='webhook', trigger_config={},
                                                        inbound_token_hash='abc')
        ai_workflows_update(3, {'trigger_type': TRIGGER_EVENT, 'trigger_config': {'hook': 'x'}}, _OWNER, False)
        self.assertIsNone(workflow.inbound_token_hash)


class TestsRunVisibility(TestCase):

    def setUp(self):
        self.can_access = MagicMock(return_value=True)
        patcher = patch(f'{_BUSINESS}.ai_workflows_entities_user_can_access', self.can_access)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_admin_should_see_every_run(self):
        self.assertTrue(ai_workflows_user_can_see_run(_ADMIN, True, _run(run_as_user_id=99)))
        self.can_access.assert_not_called()

    def test_uninvolved_user_should_not_see_the_run(self):
        self.assertFalse(ai_workflows_user_can_see_run(_OTHER, False, _run()))

    def test_triggering_user_with_entity_access_should_see_the_run(self):
        self.assertTrue(ai_workflows_user_can_see_run(_OTHER, False, _run(triggered_by_user_id=_OTHER)))

    def test_involved_user_without_entity_access_should_not_see_the_run(self):
        self.can_access.return_value = False
        self.assertFalse(ai_workflows_user_can_see_run(_OWNER, False, _run()))

    def test_owner_of_a_deleted_workflow_is_taken_from_the_snapshot(self):
        run = _run(workflow=None, run_as_user_id=99)
        self.assertTrue(ai_workflows_user_can_see_run(_OWNER, False, run))

    def test_run_without_entity_needs_no_entity_access(self):
        self.can_access.return_value = False
        self.assertTrue(ai_workflows_user_can_see_run(_OWNER, False, _run(entity_type=None, entity_id=None)))

    def test_access_cache_should_be_used(self):
        cache = {}
        ai_workflows_user_can_see_run(_OWNER, False, _run(), cache)
        ai_workflows_user_can_see_run(_OWNER, False, _run(), cache)
        self.can_access.assert_called_once()


class TestsRunEndpoints(TestCase):

    def setUp(self):
        self.run = _run(triggered_by_user_id=_OTHER)
        self.can_access = MagicMock(return_value=True)
        patchers = [
            patch(f'{_BUSINESS}.ai_workflows_db_get_run_by_uuid',
                  side_effect=lambda u: self.run if u == self.run.uuid else None),
            patch(f'{_BUSINESS}.ai_workflows_entities_user_can_access', self.can_access),
            patch(f'{_BUSINESS}._run_detail', side_effect=lambda _run, _user_id, _is_admin, everything=False: {
                'everything': everything}),
            patch(f'{_BUSINESS}.ai_workflows_db_list_inbound_events_for_run', return_value=[]),
            patch(f'{_BUSINESS}.ai_workflows_db_user_summary', return_value={}),
            patch(f'{_BUSINESS}.ai_workflows_db_get_user', return_value=None),
            patch(f'{_BUSINESS}.track_activity'),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_unknown_or_malformed_run_should_be_not_found(self):
        with self.assertRaises(ObjectNotFoundError):
            ai_workflows_get_run('nope', _OWNER, False)
        with self.assertRaises(ObjectNotFoundError):
            ai_workflows_get_run(str(uuid.uuid4()), _OWNER, False)

    def test_invisible_run_should_be_not_found(self):
        with self.assertRaises(ObjectNotFoundError):
            ai_workflows_get_run(str(self.run.uuid), 99, False)

    def test_export_should_be_limited_to_admin_and_owner(self):
        with self.assertRaises(AiWorkflowsForbiddenError):
            ai_workflows_export_run(str(self.run.uuid), _OTHER, False)
        self.assertTrue(ai_workflows_export_run(str(self.run.uuid), _OWNER, False)['everything'])

    def test_run_list_should_drop_runs_on_inaccessible_entities(self):
        rows = {1: _run(id=1, entity_id=1), 2: _run(id=2, entity_id=2)}
        self.can_access.side_effect = lambda _user_id, _entity_type, entity_id: entity_id == 1
        light = [SimpleNamespace(id=r.id, entity_type=r.entity_type, entity_id=r.entity_id) for r in rows.values()]
        with patch(f'{_BUSINESS}.ai_workflows_business_db_involved_runs', return_value=light) as involved, \
                patch(f'{_BUSINESS}.ai_workflows_business_db_runs_by_ids',
                      side_effect=lambda ids: [rows[i] for i in ids]), \
                patch(f'{_BUSINESS}.ai_workflows_run_summary', side_effect=lambda r, *_args: {'entity_id': r.entity_id}):
            result = ai_workflows_list_runs(_OWNER, False, page=1, per_page=10)
        self.assertEqual([{'entity_id': 1}], result['data'])
        self.assertEqual(1, result['total'])
        self.assertEqual(_OWNER, involved.call_args.args[0])


class TestsManualRun(_WorkflowsTestCase):

    def setUp(self):
        super().setUp()
        self.workflow = self.workflows[3] = _stored_workflow(customer_scope=[1])
        self.start_run.return_value = SimpleNamespace(uuid=uuid.uuid4())
        summary = patch(f'{_BUSINESS}.ai_workflows_run_summary', side_effect=lambda r, *_args: {'uuid': str(r.uuid)})
        summary.start()
        self.addCleanup(summary.stop)

    def test_manual_run_should_act_as_the_owner_triggered_by_the_clicking_user(self):
        ai_workflows_run_manual(3, {'entity_type': 'alert', 'entity_id': '4', 'dry_run': True}, _OWNER, False)
        kwargs = self.start_run.call_args.kwargs
        self.assertEqual(_OWNER, kwargs['run_as_user_id'])
        self.assertEqual(_OWNER, kwargs['triggered_by_user_id'])
        self.assertEqual(('alert', 4), (kwargs['entity_type'], kwargs['entity_id']))
        self.assertTrue(kwargs['dry_run'])

    def test_entity_type_outside_the_trigger_config_should_be_rejected(self):
        with self.assertRaises(BusinessProcessingError):
            ai_workflows_run_manual(3, {'entity_type': 'case', 'entity_id': 4}, _OWNER, False)
        self.start_run.assert_not_called()

    def test_entity_the_user_cannot_access_should_be_not_found(self):
        self.can_access.return_value = False
        with self.assertRaises(ObjectNotFoundError):
            ai_workflows_run_manual(3, {'entity_type': 'alert', 'entity_id': 4}, _OWNER, False)
        self.start_run.assert_not_called()

    def test_entity_outside_the_customer_scope_should_be_rejected(self):
        with patch(f'{_BUSINESS}.ai_workflows_db_entity_customer', return_value=2):
            with self.assertRaises(BusinessProcessingError):
                ai_workflows_run_manual(3, {'entity_type': 'alert', 'entity_id': 4}, _OWNER, False)
        self.start_run.assert_not_called()

    def test_missing_entity_should_be_rejected_when_the_trigger_needs_one(self):
        with self.assertRaises(BusinessProcessingError):
            ai_workflows_run_manual(3, {}, _OWNER, False)

    def test_disabled_feature_should_refuse_runs(self):
        with patch.dict(app.config, {'AI_WORKFLOWS_ENABLED': False}):
            with self.assertRaises(BusinessProcessingError):
                ai_workflows_run_manual(3, {'entity_type': 'alert', 'entity_id': 4}, _OWNER, False)


class TestsCatalogue(TestCase):

    def test_llm_status_should_never_carry_the_key(self):
        config = SimpleNamespace(enabled=True, provider='openai', model='gpt', api_key='sk-secret')
        with patch(f'{_BUSINESS}.load_config', return_value=config), \
                patch(f'{_BUSINESS}.ai_workflows_keystore_visible_entries', return_value=[]), \
                patch(f'{_BUSINESS}.ai_workflows_nodes_catalogue', return_value=[]), \
                patch(f'{_BUSINESS}.ai_workflows_tools_catalogue', return_value=[]), \
                patch(f'{_BUSINESS}.ai_workflows_db_postload_hooks', return_value=[('on_postload_x', 'X')]):
            catalogue = ai_workflows_catalogue(_OWNER)
        self.assertEqual({'enabled': True, 'provider': 'openai', 'model': 'gpt'}, catalogue['llm'])
        self.assertNotIn('sk-secret', repr(catalogue))
        self.assertEqual([{'name': 'on_postload_x', 'description': 'X'}], catalogue['hooks'])
