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

"""Who a run acts as: always the workflow owner, bounded by the API-key
scope of the triggering credential, and never a deactivated user or one
without MCP rights."""

import contextlib
from types import SimpleNamespace
from unittest.mock import patch

from flask import g

from app import app
from app.iris_engine.ai_workflows.engine import ai_workflows_engine_start_run
from app.iris_engine.ai_workflows.identity import AiWorkflowIdentityError
from app.iris_engine.ai_workflows.identity import ai_workflows_identity
from app.iris_engine.ai_workflows.identity import ai_workflows_identity_check_user
from app.models.authorization import Permissions
from tests.app.iris_engine.ai_workflows.harness import OWNER_ID
from tests.app.iris_engine.ai_workflows.harness import EngineTestCase
from tests.app.iris_engine.ai_workflows.harness import chain
from tests.app.iris_engine.ai_workflows.harness import workflow

_ENGINE = 'app.iris_engine.ai_workflows.engine'
_IDENTITY = 'app.iris_engine.ai_workflows.identity'


def _vars_node(node_id='vars'):
    return {'id': node_id, 'type': 'set_variables', 'config': {'variables': [{'name': 'a', 'value': '1'}]}}


class TestsRunActsAsOwner(EngineTestCase):

    def test_run_should_act_as_the_owner_whoever_asks(self):
        run = ai_workflows_engine_start_run(workflow(chain()), 'manual', run_as_user_id=77, triggered_by_user_id=77,
                                            entity_type='case', entity_id=42, scope_mask=0x1)
        self.assertEqual((OWNER_ID, OWNER_ID, 77, 0x1),
                         (run.run_as_user_id, run.owner_id, run.triggered_by_user_id, run.scope_mask))
        self.assertEqual('running', run.status)

    def test_run_without_key_scope_should_be_unrestricted(self):
        run = self.start(workflow(chain()))
        self.assertEqual((OWNER_ID, None, []), (run.owner_id, run.scope_mask, run.used_key_names))

    def test_keystore_should_resolve_as_the_run_as_user(self):
        resolved_for = []

        def _resolver(user_id):
            resolved_for.append(user_id)
            return self.resolver

        with patch(f'{_ENGINE}.ai_workflows_keystore_resolver', _resolver):
            run = self.run_to_rest(workflow(chain(_vars_node())))
        self.assertEqual('succeeded', run.status)
        self.assertTrue(resolved_for)
        self.assertEqual({OWNER_ID}, set(resolved_for))

    def test_key_scope_without_read_permission_should_refuse_the_entity(self):
        wf = workflow(chain())
        run = ai_workflows_engine_start_run(wf, 'manual', run_as_user_id=OWNER_ID, entity_type='alert', entity_id=5,
                                            scope_mask=Permissions.war_rooms_read.value)
        self.assertEqual('skipped', run.status)
        self.assertIn('API key', run.error)
        self.assertEqual([], self.enqueued)
        run = ai_workflows_engine_start_run(wf, 'manual', run_as_user_id=OWNER_ID, entity_type='alert', entity_id=5,
                                            scope_mask=Permissions.alerts_read.value)
        self.assertEqual('running', run.status)

    def test_owner_without_mcp_rights_should_not_start(self):
        user = SimpleNamespace(id=OWNER_ID, active=True, mcp_allowed=False, user='owner')
        with patch(f'{_ENGINE}.ai_workflows_db_get_user', lambda _user_id: user):
            run = self.start(workflow(chain()))
        self.assertEqual('skipped', run.status)
        self.assertIn('MCP is disabled', run.error)

    def test_deactivated_owner_should_not_start(self):
        user = SimpleNamespace(id=OWNER_ID, active=False, user='owner')
        with patch(f'{_ENGINE}.ai_workflows_db_get_user', lambda _user_id: user):
            run = self.start(workflow(chain()))
        self.assertEqual('skipped', run.status)
        self.assertIn('deactivated', run.error)

    def test_identity_refused_mid_run_should_fail_the_run(self):
        @contextlib.contextmanager
        def _refused(_user_id, _run=None):
            raise AiWorkflowIdentityError('MCP is disabled for user owner')
            yield  # pragma: no cover

        graph = chain(_vars_node(), ports={})
        with patch(f'{_ENGINE}.ai_workflows_identity', _refused):
            run = self.run_to_rest(workflow(graph))
        self.assertEqual('failed', run.status)
        self.assertIn('cannot act as its user', run.error)
        self.assertEqual(['failed'], [s.status for s in self.steps(run)])
        self.assertEqual([run], self.published)


class TestsIdentityScope(EngineTestCase):

    def setUp(self):
        super().setUp()
        self.user = SimpleNamespace(id=OWNER_ID, active=True, user='owner', name='Owner', email='o@example.org')
        for target, replacement in ((f'{_IDENTITY}.ai_workflows_db_get_user', lambda _user_id: self.user),
                                    (f'{_IDENTITY}.ac_get_effective_permissions_of_user', lambda _user: 0b1111)):
            patcher = patch(target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_run_scope_mask_should_bound_the_permissions(self):
        run = SimpleNamespace(id=1, chain_depth=0, scope_mask=0b0101)
        with app.test_request_context('/'):
            with ai_workflows_identity(OWNER_ID, run):
                self.assertEqual(0b0101, g.auth_user_permissions)
                self.assertEqual(0b0101, g.api_key_row.scope_mask)
            self.assertIsNone(getattr(g, 'api_key_row', None))

    def test_no_scope_should_keep_the_user_permissions(self):
        with app.test_request_context('/'):
            with ai_workflows_identity(OWNER_ID, SimpleNamespace(id=1, chain_depth=0, scope_mask=None)):
                self.assertEqual(0b1111, g.auth_user_permissions)
                self.assertIsNone(getattr(g, 'api_key_row', None))

    def test_caller_key_scope_should_apply_when_acting_as_itself(self):
        with app.test_request_context('/'):
            g.auth_token_user_id = OWNER_ID
            g.api_key_row = SimpleNamespace(scope_mask=0b0011)
            with ai_workflows_identity(OWNER_ID, SimpleNamespace(id=1, chain_depth=0, scope_mask=0b0110)):
                self.assertEqual(0b0010, g.auth_user_permissions)
            # The caller's request state is restored
            self.assertEqual(0b0011, g.api_key_row.scope_mask)

    def test_mcp_disabled_user_should_be_refused(self):
        self.user.mcp_allowed = False
        with app.test_request_context('/'):
            with self.assertRaises(AiWorkflowIdentityError):
                with ai_workflows_identity(OWNER_ID):
                    pass  # pragma: no cover

    def test_check_user_should_refuse_missing_or_inactive_users(self):
        for user in (None, SimpleNamespace(id=1, active=False), SimpleNamespace(id=1, active=True, mcp_allowed=False)):
            with self.assertRaises(AiWorkflowIdentityError):
                ai_workflows_identity_check_user(user)
        ai_workflows_identity_check_user(SimpleNamespace(id=1, active=True, mcp_allowed=True))
