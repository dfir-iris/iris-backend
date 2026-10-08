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

"""A user who owns AI workflows cannot be deleted: the workflows would
otherwise lose the identity their runs act as."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from app.business.users import users_delete
from app.models.errors import BusinessProcessingError

_BUSINESS = 'app.business.users'


class TestsUsersDeleteAiWorkflowOwner(TestCase):

    def setUp(self):
        self.user = SimpleNamespace(id=12, active=False)
        patchers = {
            'comments': patch(f'{_BUSINESS}.user_has_comments', return_value=False),
            'count': patch(f'{_BUSINESS}.ai_workflows_business_db_count_owned', return_value=0),
            'delete': patch(f'{_BUSINESS}.delete_user'),
            'track': patch(f'{_BUSINESS}.track_activity'),
        }
        self.mocks = {}
        for name, patcher in patchers.items():
            self.mocks[name] = patcher.start()
            self.addCleanup(patcher.stop)

    def test_owner_of_workflows_should_not_be_deleted(self):
        self.mocks['count'].return_value = 3
        with self.assertRaises(BusinessProcessingError) as context:
            users_delete(self.user)
        self.assertEqual('User owns 3 AI workflows; transfer ownership first', context.exception.get_message())
        self.mocks['count'].assert_called_once_with(12)
        self.mocks['delete'].assert_not_called()

    def test_user_without_workflows_should_be_deleted(self):
        users_delete(self.user)
        self.mocks['delete'].assert_called_once_with(12)
