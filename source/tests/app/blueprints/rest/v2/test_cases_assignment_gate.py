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

"""`PUT /api/v2/cases/{id}` must not assign a case to someone who can't open it.

`owner_id` and `reviewer_id` put a case in front of the user they name:
`/api/v2/dashboard/cases/list` and `/reviews/list` select on those columns,
and a `case_assigned` notification carries the case name. Neither column
grants access, so without a gate a full-access caller could push a case at
any user id on the server.

The gate lives in `CasesOperations.update` and runs *before* `schema.load`.
That ordering is the point: the schema is `load_instance=True` and loads with
`instance=case`, so a rejected assignment that reached the load would still
have written the rest of the payload onto the row before the error surfaced.

Driven through `cases_operations.update` in a request context — the only
seam where ordering is observable. No DB: everything past the gate is
patched at the module boundary `v2/cases.py` imports it through.
"""

import json
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from app import app
from app.blueprints.rest.v2.cases import cases_operations
from app.models.authorization import CaseAccessLevel

_CASE_IDENTIFIER = 11
_ASSIGNEE_IDENTIFIER = 23
_CUSTOMER_IDENTIFIER = 1

_CASES_MODULE = 'app.blueprints.rest.v2.cases'


class _AssignmentGateTestCase(TestCase):
    """Everything past the gate is stubbed; only the gate is real."""

    def setUp(self):
        self.case = SimpleNamespace(
            case_id=_CASE_IDENTIFIER,
            client_id=_CUSTOMER_IDENTIFIER,
            state_id=1,
            reviewer_id=None
        )

        patch(f'{_CASES_MODULE}.cases_get_by_identifier', return_value=self.case).start()
        patch(f'{_CASES_MODULE}.ac_fast_check_current_user_has_case_access',
              return_value=CaseAccessLevel.full_access.value).start()
        patch(f'{_CASES_MODULE}.ac_current_user_has_customer_access', return_value=True).start()

        # The gate itself. `None` is what `ac_fast_check_user_has_case_access`
        # returns for a user with no access — the default here, so a test
        # that wants the allowing branch has to say so.
        self.gate = patch(f'{_CASES_MODULE}.ac_fast_check_user_has_case_access',
                          return_value=None).start()

        self.cases_update = patch(f'{_CASES_MODULE}.cases_update', return_value=self.case).start()

        schema_class = patch(f'{_CASES_MODULE}.CaseSchemaForAPIV2').start()
        self.schema = schema_class.return_value
        self.schema.load.return_value = self.case
        self.schema.dump.return_value = {'case_id': _CASE_IDENTIFIER}

        self.addCleanup(patch.stopall)

    def _update(self, body):
        with app.test_request_context(f'/api/v2/cases/{_CASE_IDENTIFIER}', method='PUT', json=body):
            return cases_operations.update(_CASE_IDENTIFIER)

    def _message(self, response):
        return json.loads(response.get_data(as_text=True))['message']


class TestRejectedAssignments(_AssignmentGateTestCase):

    def test_owner_without_case_access_is_rejected(self):
        response = self._update({'owner_id': _ASSIGNEE_IDENTIFIER})
        self.assertEqual(400, response.status_code)
        self.assertEqual('Invalid owner_id', self._message(response))

    def test_reviewer_without_case_access_is_rejected(self):
        response = self._update({'reviewer_id': _ASSIGNEE_IDENTIFIER})
        self.assertEqual(400, response.status_code)
        self.assertEqual('Invalid reviewer_id', self._message(response))

    def test_a_bad_reviewer_is_rejected_even_when_the_owner_is_fine(self):
        # Both columns are gated, not just the first one to be looked at.
        self.gate.side_effect = [CaseAccessLevel.full_access.value, None]
        response = self._update({
            'owner_id': _ASSIGNEE_IDENTIFIER,
            'reviewer_id': _ASSIGNEE_IDENTIFIER + 1
        })
        self.assertEqual(400, response.status_code)
        self.assertEqual('Invalid reviewer_id', self._message(response))

    def test_the_gate_is_asked_about_the_assignee_and_full_access(self):
        # Not about the caller: the caller's access was already checked at
        # the top of update(). This asks whether the *assignee* can open it.
        self._update({'owner_id': _ASSIGNEE_IDENTIFIER})
        self.gate.assert_called_once_with(_ASSIGNEE_IDENTIFIER, _CASE_IDENTIFIER,
                                          [CaseAccessLevel.full_access])

    def test_a_numeric_string_assignee_is_still_gated(self):
        # JSON from the UI is typed, but the API takes what it is given.
        self._update({'owner_id': str(_ASSIGNEE_IDENTIFIER)})
        self.gate.assert_called_once_with(_ASSIGNEE_IDENTIFIER, _CASE_IDENTIFIER,
                                          [CaseAccessLevel.full_access])

    def test_a_non_integer_owner_is_rejected_without_reaching_the_gate(self):
        response = self._update({'owner_id': 'administrator'})
        self.assertEqual(400, response.status_code)
        self.assertEqual('Invalid owner_id', self._message(response))
        self.gate.assert_not_called()

    def test_a_list_owner_is_rejected_without_reaching_the_gate(self):
        response = self._update({'owner_id': [_ASSIGNEE_IDENTIFIER]})
        self.assertEqual(400, response.status_code)
        self.gate.assert_not_called()

    def test_a_rejected_owner_never_reaches_the_schema_load(self):
        # The security-relevant assertion. `schema.load(instance=case)`
        # mutates the row in place, so reaching it on a rejected assignment
        # would leave the case half-updated behind a 400.
        self._update({'owner_id': _ASSIGNEE_IDENTIFIER, 'case_name': 'renamed by the attacker'})
        self.schema.load.assert_not_called()

    def test_a_rejected_reviewer_never_reaches_the_schema_load(self):
        self._update({'reviewer_id': _ASSIGNEE_IDENTIFIER, 'case_name': 'renamed by the attacker'})
        self.schema.load.assert_not_called()

    def test_a_rejected_assignment_never_reaches_cases_update(self):
        self._update({'owner_id': _ASSIGNEE_IDENTIFIER})
        self.cases_update.assert_not_called()


class TestAcceptedAssignments(_AssignmentGateTestCase):
    """The gate must not be collateral damage for legitimate updates."""

    def test_an_assignee_holding_full_access_is_accepted(self):
        self.gate.return_value = CaseAccessLevel.full_access.value
        response = self._update({'owner_id': _ASSIGNEE_IDENTIFIER})
        self.assertEqual(200, response.status_code)
        self.schema.load.assert_called_once()
        self.cases_update.assert_called_once()

    def test_an_update_that_touches_no_assignment_does_not_call_the_gate(self):
        response = self._update({'case_name': 'new name'})
        self.assertEqual(200, response.status_code)
        self.gate.assert_not_called()

    def test_unassigning_an_owner_with_null_does_not_call_the_gate(self):
        response = self._update({'owner_id': None})
        self.assertEqual(200, response.status_code)
        self.gate.assert_not_called()

    def test_unassigning_a_reviewer_with_an_empty_string_does_not_call_the_gate(self):
        # The empty string is how the UI clears the reviewer — update()
        # normalises it to None further down.
        response = self._update({'reviewer_id': ''})
        self.assertEqual(200, response.status_code)
        self.gate.assert_not_called()
