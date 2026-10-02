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

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from app.business.cases_access import cases_access_set_group
from app.business.cases_access import cases_access_set_user
from app.models.authorization import CaseAccessLevel
from app.models.errors import BusinessProcessingError

_MODULE = 'app.business.cases_access'


class TestCasesAccess(TestCase):

    def setUp(self):
        self.case = SimpleNamespace(case_id=11)
        self.user = SimpleNamespace(id=23, name='target')
        self.members = [{'id': 23}, {'id': 24}]
        self.group = SimpleNamespace(group_id=5, group_name='analysts', group_members=self.members,
                                     group_auto_follow=True)

        self.add_user = patch(f'{_MODULE}.add_case_access_to_user', return_value=(self.user, 'Updated')).start()
        self.add_group = patch(f'{_MODULE}.add_case_access_to_group', return_value=(self.group, 'Updated')).start()
        self.recompute = patch(f'{_MODULE}.ac_recompute_effective_ac_from_users_list').start()
        self.history = patch(f'{_MODULE}.add_obj_history_entry').start()
        self.activity = patch(f'{_MODULE}.track_activity').start()

        self.addCleanup(patch.stopall)

    def test_sets_the_user_access_on_the_case_and_records_it(self):
        level = cases_access_set_user(self.case, self.user, CaseAccessLevel.read_only.value)

        self.assertEqual(CaseAccessLevel.read_only, level)
        self.add_user.assert_called_once_with(self.user, [11], CaseAccessLevel.read_only.value)
        self.history.assert_called_once_with(self.case, 'access changed to read_only for user target', commit=True)
        self.activity.assert_called_once_with('access changed to read_only for user target', caseid=11)

    def test_sets_the_group_access_and_recomputes_its_members(self):
        cases_access_set_group(self.case, self.group, CaseAccessLevel.full_access.value)

        self.add_group.assert_called_once_with(self.group, [11], CaseAccessLevel.full_access.value)
        self.recompute.assert_called_once_with(self.members)
        self.assertTrue(self.group.group_auto_follow)

    def test_rejects_anything_but_a_case_access_level(self):
        for access_level in (None, '4', 3, 8, 0, True, 4.0):
            with self.subTest(access_level=access_level):
                with self.assertRaises(BusinessProcessingError):
                    cases_access_set_user(self.case, self.user, access_level)
                with self.assertRaises(BusinessProcessingError):
                    cases_access_set_group(self.case, self.group, access_level)

        self.add_user.assert_not_called()
        self.add_group.assert_not_called()

    def test_a_failed_write_is_not_recorded(self):
        self.add_user.return_value = (None, 'Invalid case ID')

        with self.assertRaises(BusinessProcessingError):
            cases_access_set_user(self.case, self.user, CaseAccessLevel.full_access.value)

        self.history.assert_not_called()
        self.activity.assert_not_called()
