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

"""Unit tests for the batched case access check (no database)."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import call
from unittest.mock import patch

from app.business.access_controls import ac_fast_check_user_has_cases_access
from app.models.authorization import CaseAccessLevel
from app.models.authorization import Permissions

_MODULE = 'app.business.access_controls'
_READ = [CaseAccessLevel.read_only, CaseAccessLevel.full_access]
_FULL = CaseAccessLevel.full_access.value
_RO = CaseAccessLevel.read_only.value
_DENY = CaseAccessLevel.deny_all.value


class TestBatchedCaseAccess(TestCase):

    def setUp(self):
        self.effective = patch(f'{_MODULE}.get_cases_effective_access', return_value={}).start()
        self.client = patch(f'{_MODULE}.check_ua_cases_client', return_value={}).start()
        self.materialise = patch(f'{_MODULE}.set_case_effective_access_for_user').start()
        self.get_user = patch('app.datamgmt.manage.manage_users_db.get_user',
                              return_value=SimpleNamespace(id=7)).start()
        self.permissions = patch('app.iris_engine.access_control.utils.ac_get_effective_permissions_of_user',
                                 return_value=Permissions.standard_user.value).start()
        self.addCleanup(patch.stopall)

    def test_empty_input_makes_no_query(self):
        self.assertEqual(ac_fast_check_user_has_cases_access(7, [], _READ), {})
        self.effective.assert_not_called()

    def test_effective_accesses_are_filtered_by_level_in_one_query(self):
        self.effective.return_value = {1: _FULL, 2: _RO, 3: _DENY | _FULL}
        result = ac_fast_check_user_has_cases_access(7, [1, 2, 3, 1], _READ)
        self.assertEqual(result, {1: _FULL, 2: _RO})
        self.effective.assert_called_once_with(7, [1, 2, 3])
        self.client.assert_not_called()
        self.assertEqual(ac_fast_check_user_has_cases_access(7, [1, 2], [CaseAccessLevel.full_access]), {1: _FULL})

    def test_missing_cases_fall_back_to_customer_membership_and_are_materialised(self):
        self.effective.return_value = {1: _RO}
        self.client.return_value = {2: _FULL}
        result = ac_fast_check_user_has_cases_access(7, [1, 2, 3], _READ)
        self.assertEqual(result, {1: _RO, 2: _FULL})
        self.client.assert_called_once_with(7, [2, 3])
        self.assertEqual(self.materialise.call_args_list, [call(7, 2, _FULL)])

    def test_no_fallback_without_app_permissions(self):
        self.permissions.return_value = 0
        self.client.return_value = {2: _FULL}
        self.assertEqual(ac_fast_check_user_has_cases_access(7, [2], _READ), {})
        self.client.assert_not_called()
        self.materialise.assert_not_called()

    def test_no_fallback_for_unknown_user(self):
        self.get_user.return_value = None
        self.assertEqual(ac_fast_check_user_has_cases_access(7, [2], _READ), {})
        self.client.assert_not_called()

    def test_materialised_deny_all_is_not_granted(self):
        self.client.return_value = {2: _DENY}
        self.assertEqual(ac_fast_check_user_has_cases_access(7, [2], _READ), {})
        self.materialise.assert_called_once_with(7, 2, _DENY)
