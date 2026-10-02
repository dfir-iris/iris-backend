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

"""`/api/v2/cases/{id}/access/*` — changing who can open a case, from the case.

Allowed to server administrators, and to holders of `case_access_manage`
who have full access to that case. The permission decorator in front of
the routes is `ac_api_requires`, covered by the REST suite; these tests
drive the view bodies through `__wrapped__`, with the case-level gate and
the guards real and everything they call patched at the `v2/cases.py`
module boundary. No DB.
"""

import json
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from app import app
from app.blueprints.rest.v2.cases import _current_user_can_manage_case_access
from app.blueprints.rest.v2.cases import get_case_access_me
from app.blueprints.rest.v2.cases import list_case_access_groups
from app.blueprints.rest.v2.cases import set_case_access_group
from app.blueprints.rest.v2.cases import set_case_access_user
from app.models.authorization import CaseAccessLevel
from app.models.authorization import Permissions
from app.models.errors import ObjectNotFoundError

_CASE_IDENTIFIER = 11
_CUSTOMER_IDENTIFIER = 3
_CALLER_IDENTIFIER = 7
_TARGET_IDENTIFIER = 23
_GROUP_IDENTIFIER = 5

_CASES_MODULE = 'app.blueprints.rest.v2.cases'

_FULL = CaseAccessLevel.full_access.value
_READ = CaseAccessLevel.read_only.value


class _CaseAccessTestCase(TestCase):

    def setUp(self):
        self.case = SimpleNamespace(case_id=_CASE_IDENTIFIER, client_id=_CUSTOMER_IDENTIFIER)
        self.permissions = {Permissions.case_access_manage}
        self.case_level = _FULL

        patch(f'{_CASES_MODULE}.iris_current_user', SimpleNamespace(id=_CALLER_IDENTIFIER)).start()
        patch(f'{_CASES_MODULE}.ac_current_user_has_permission',
              side_effect=lambda permission: permission in self.permissions).start()
        patch(f'{_CASES_MODULE}.ac_fast_check_current_user_has_case_access',
              side_effect=lambda _identifier, levels: self.case_level
              if any(self.case_level & level.value for level in levels) else None).start()
        patch(f'{_CASES_MODULE}.cases_get_by_identifier', return_value=self.case).start()
        # `ac_api_return_access_denied` logs through the request's user.
        patch(f'{_CASES_MODULE}.ac_api_return_access_denied',
              side_effect=lambda **_kwargs: app.response_class(status=403)).start()

        self.target = SimpleNamespace(id=_TARGET_IDENTIFIER, name='target')
        self.get_user = patch(f'{_CASES_MODULE}.cases_access_get_user', return_value=self.target).start()
        self.sees_customer = patch(f'{_CASES_MODULE}.cases_access_user_sees_customer',
                                   return_value=True).start()
        self.set_user = patch(f'{_CASES_MODULE}.cases_access_set_user',
                              side_effect=lambda _case, _user, level: CaseAccessLevel(level)).start()

        self.group = SimpleNamespace(group_id=_GROUP_IDENTIFIER, group_name='group',
                                     group_members=[{'id': _TARGET_IDENTIFIER}])
        patch(f'{_CASES_MODULE}.cases_access_get_group', return_value=self.group).start()
        self.set_group = patch(f'{_CASES_MODULE}.cases_access_set_group',
                               side_effect=lambda _case, _group, level: CaseAccessLevel(level)).start()
        self.list_groups = patch(f'{_CASES_MODULE}.cases_access_list_groups', return_value=[]).start()

        self.addCleanup(patch.stopall)

    def _post(self, view, path, body):
        with app.test_request_context(f'/api/v2/cases/{_CASE_IDENTIFIER}/access/{path}', method='POST', json=body):
            return view.__wrapped__(_CASE_IDENTIFIER)

    def _set_user(self, body):
        return self._post(set_case_access_user, 'users', body)

    def _set_group(self, body):
        return self._post(set_case_access_group, 'groups', body)

    @staticmethod
    def _json(response):
        return json.loads(response.get_data(as_text=True))


class TestWhoMayManage(_CaseAccessTestCase):

    def test_permission_and_full_access_may_manage(self):
        with app.test_request_context('/'):
            self.assertTrue(_current_user_can_manage_case_access(_CASE_IDENTIFIER))

    def test_permission_with_read_only_access_may_not_manage(self):
        self.case_level = _READ
        with app.test_request_context('/'):
            self.assertFalse(_current_user_can_manage_case_access(_CASE_IDENTIFIER))

    def test_permission_without_case_access_may_not_manage(self):
        self.case_level = CaseAccessLevel.deny_all.value
        with app.test_request_context('/'):
            self.assertFalse(_current_user_can_manage_case_access(_CASE_IDENTIFIER))

    def test_full_access_without_the_permission_may_not_manage(self):
        self.permissions = set()
        with app.test_request_context('/'):
            self.assertFalse(_current_user_can_manage_case_access(_CASE_IDENTIFIER))

    def test_server_administrator_may_manage_without_case_access(self):
        self.permissions = {Permissions.server_administrator}
        self.case_level = CaseAccessLevel.deny_all.value
        with app.test_request_context('/'):
            self.assertTrue(_current_user_can_manage_case_access(_CASE_IDENTIFIER))

    def test_read_only_caller_is_refused_and_nothing_is_written(self):
        self.case_level = _READ
        response = self._set_user({'user_id': _TARGET_IDENTIFIER, 'access_level': _FULL})
        self.assertEqual(403, response.status_code)
        self.set_user.assert_not_called()

    def test_read_only_caller_cannot_list_groups(self):
        self.case_level = _READ
        with app.test_request_context(f'/api/v2/cases/{_CASE_IDENTIFIER}/access/groups'):
            response = list_case_access_groups.__wrapped__(_CASE_IDENTIFIER)
        self.assertEqual(403, response.status_code)
        self.list_groups.assert_not_called()

    def test_access_me_reports_whether_the_caller_may_manage(self):
        patch(f'{_CASES_MODULE}.cases_exists', return_value=True).start()
        with app.test_request_context(f'/api/v2/cases/{_CASE_IDENTIFIER}/access/me'):
            managing = self._json(get_case_access_me.__wrapped__(_CASE_IDENTIFIER))
        self.permissions = set()
        with app.test_request_context(f'/api/v2/cases/{_CASE_IDENTIFIER}/access/me'):
            not_managing = self._json(get_case_access_me.__wrapped__(_CASE_IDENTIFIER))

        self.assertEqual({'access_level': _FULL, 'can_manage_access': True}, managing)
        self.assertEqual({'access_level': _FULL, 'can_manage_access': False}, not_managing)

    def test_unknown_case_is_not_found(self):
        patch(f'{_CASES_MODULE}.cases_get_by_identifier', side_effect=ObjectNotFoundError()).start()
        response = self._set_user({'user_id': _TARGET_IDENTIFIER, 'access_level': _FULL})
        self.assertEqual(404, response.status_code)


class TestSetUserAccess(_CaseAccessTestCase):

    def test_grants_the_user(self):
        response = self._set_user({'user_id': _TARGET_IDENTIFIER, 'access_level': _READ})
        self.assertEqual(200, response.status_code)
        self.assertEqual({'user_id': _TARGET_IDENTIFIER, 'access_level': _READ}, self._json(response))
        self.set_user.assert_called_once_with(self.case, self.target, _READ)

    def test_nobody_changes_their_own_access(self):
        for permissions in ({Permissions.case_access_manage}, {Permissions.server_administrator}):
            self.permissions = permissions
            response = self._set_user({'user_id': _CALLER_IDENTIFIER, 'access_level': _READ})
            self.assertEqual(400, response.status_code)
        self.set_user.assert_not_called()

    def test_refuses_a_user_who_cannot_see_the_customer(self):
        self.sees_customer.return_value = False
        response = self._set_user({'user_id': _TARGET_IDENTIFIER, 'access_level': _FULL})
        self.assertEqual(400, response.status_code)
        self.sees_customer.assert_called_once_with(_TARGET_IDENTIFIER, _CUSTOMER_IDENTIFIER)
        self.set_user.assert_not_called()

    def test_administrator_is_not_held_to_the_customer_guard(self):
        self.permissions = {Permissions.server_administrator}
        self.sees_customer.return_value = False
        response = self._set_user({'user_id': _TARGET_IDENTIFIER, 'access_level': _FULL})
        self.assertEqual(200, response.status_code)

    def test_unknown_user_is_rejected(self):
        self.get_user.side_effect = ObjectNotFoundError()
        response = self._set_user({'user_id': _TARGET_IDENTIFIER, 'access_level': _FULL})
        self.assertEqual(400, response.status_code)
        self.assertEqual('Invalid user_id', self._json(response)['message'])

    def test_non_integer_user_is_rejected(self):
        for user_identifier in ('23', None, True, [23]):
            response = self._set_user({'user_id': user_identifier, 'access_level': _FULL})
            self.assertEqual(400, response.status_code)
        self.get_user.assert_not_called()


class TestSetGroupAccess(_CaseAccessTestCase):

    def test_grants_the_group(self):
        response = self._set_group({'group_id': _GROUP_IDENTIFIER, 'access_level': _READ})
        self.assertEqual(200, response.status_code)
        self.assertEqual({'group_id': _GROUP_IDENTIFIER, 'access_level': _READ}, self._json(response))
        self.set_group.assert_called_once_with(self.case, self.group, _READ)

    def test_may_not_lower_a_group_the_caller_belongs_to(self):
        self.group.group_members.append({'id': _CALLER_IDENTIFIER})
        response = self._set_group({'group_id': _GROUP_IDENTIFIER, 'access_level': _READ})
        self.assertEqual(400, response.status_code)
        self.set_group.assert_not_called()

    def test_may_keep_a_group_the_caller_belongs_to_at_full_access(self):
        self.group.group_members.append({'id': _CALLER_IDENTIFIER})
        response = self._set_group({'group_id': _GROUP_IDENTIFIER, 'access_level': _FULL})
        self.assertEqual(200, response.status_code)

    def test_refuses_a_group_with_a_member_who_cannot_see_the_customer(self):
        self.sees_customer.return_value = False
        response = self._set_group({'group_id': _GROUP_IDENTIFIER, 'access_level': _FULL})
        self.assertEqual(400, response.status_code)
        self.set_group.assert_not_called()

    def test_administrator_is_not_held_to_the_member_guards(self):
        self.permissions = {Permissions.server_administrator}
        self.sees_customer.return_value = False
        self.group.group_members.append({'id': _CALLER_IDENTIFIER})
        response = self._set_group({'group_id': _GROUP_IDENTIFIER, 'access_level': _READ})
        self.assertEqual(200, response.status_code)
