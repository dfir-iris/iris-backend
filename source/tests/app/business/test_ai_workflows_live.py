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

"""Who may follow the live progress of AI workflow runs: the room of a
run needs the run to be visible, the room of a workflow needs to own it
(or to be an administrator), both need an AI workflows permission."""

import uuid
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from app import app
from app.business.ai_workflows import AiWorkflowsForbiddenError
from app.business.ai_workflows import ai_workflows_live_room
from app.models.authorization import Permissions
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError

_BUSINESS = 'app.business.ai_workflows'
_ANALYST = 12
_OWNER = 13
_RUN_UUID = uuid.uuid4()


class TestsLiveRoom(TestCase):

    def setUp(self):
        self.permissions = Permissions.ai_workflows_read.value
        self.run = SimpleNamespace(id=3, uuid=_RUN_UUID, workflow_id=7, run_as_user_id=_OWNER,
                                   triggered_by_user_id=None, owner_id=_OWNER, workflow=None,
                                   definition_snapshot={}, entity_type=None, entity_id=None)
        self.workflow = SimpleNamespace(id=7, owner_id=_OWNER)
        patchers = [
            patch.dict(app.config, {'AI_WORKFLOWS_ENABLED': True}),
            patch(f'{_BUSINESS}.ai_workflows_db_get_user',
                  side_effect=lambda user_id: SimpleNamespace(id=user_id, active=True)),
            patch(f'{_BUSINESS}.ac_get_effective_permissions_of_user', side_effect=lambda _user: self.permissions),
            patch(f'{_BUSINESS}.ai_workflows_db_get_run_by_uuid',
                  side_effect=lambda value: self.run if value == _RUN_UUID else None),
            patch(f'{_BUSINESS}.ai_workflows_db_get',
                  side_effect=lambda workflow_id: self.workflow if workflow_id == 7 else None),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_involved_user_should_follow_the_run(self):
        self.assertEqual(f'ai-workflow-run-{_RUN_UUID}', ai_workflows_live_room(_OWNER, run_uuid=str(_RUN_UUID)))

    def test_other_user_should_not_follow_the_run(self):
        with self.assertRaises(ObjectNotFoundError):
            ai_workflows_live_room(_ANALYST, run_uuid=str(_RUN_UUID))

    def test_unknown_run_should_not_exist(self):
        with self.assertRaises(ObjectNotFoundError):
            ai_workflows_live_room(_OWNER, run_uuid='not-a-uuid')

    def test_owner_should_follow_the_workflow(self):
        self.assertEqual('ai-workflow-7', ai_workflows_live_room(_OWNER, workflow_id=7))

    def test_other_user_should_not_follow_the_workflow(self):
        with self.assertRaises(ObjectNotFoundError):
            ai_workflows_live_room(_ANALYST, workflow_id=7)

    def test_administrator_should_follow_anything(self):
        self.permissions = Permissions.server_administrator.value
        self.assertEqual('ai-workflow-7', ai_workflows_live_room(_ANALYST, workflow_id=7))
        self.assertEqual(f'ai-workflow-run-{_RUN_UUID}', ai_workflows_live_room(_ANALYST, run_uuid=str(_RUN_UUID)))

    def test_user_without_the_permission_should_be_refused(self):
        self.permissions = 0
        with self.assertRaises(AiWorkflowsForbiddenError):
            ai_workflows_live_room(_OWNER, workflow_id=7)

    def test_something_to_follow_should_be_named(self):
        with self.assertRaises(BusinessProcessingError):
            ai_workflows_live_room(_OWNER)
