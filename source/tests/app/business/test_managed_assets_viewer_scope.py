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

"""Unit tests for the server administrator's cross-case viewer scope:
unrestricted, except for the cases that explicitly deny them."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from app.business.managed_assets import managed_assets_viewer_scope
from app.models.authorization import Permissions

_UTILS = 'app.iris_engine.access_control.utils'
_ADMIN = Permissions.server_administrator.value


class TestAdministratorViewerScope(TestCase):

    def test_administrator_without_denial_sees_all(self):
        with patch(f'{_UTILS}.ac_get_administrator_readable_case_ids', return_value=None):
            scope = managed_assets_viewer_scope(SimpleNamespace(id=1), _ADMIN)
        self.assertIsNone(scope.get_case_ids())
        self.assertIsNone(scope.get_client_ids())
        self.assertTrue(scope.is_administrator())

    def test_administrator_denied_cases_are_excluded(self):
        with patch(f'{_UTILS}.ac_get_administrator_readable_case_ids', return_value=[1, 3]) as readable:
            scope = managed_assets_viewer_scope(SimpleNamespace(id=7), _ADMIN)
        readable.assert_called_once_with(7)
        self.assertEqual(scope.get_case_ids(), [1, 3])
        self.assertTrue(scope.has_case_access())

    def test_administrator_denied_everything_has_no_case_access(self):
        with patch(f'{_UTILS}.ac_get_administrator_readable_case_ids', return_value=[]):
            scope = managed_assets_viewer_scope(SimpleNamespace(id=7), _ADMIN)
        self.assertFalse(scope.has_case_access())
