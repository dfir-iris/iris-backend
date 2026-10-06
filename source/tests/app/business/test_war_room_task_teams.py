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

"""Unit tests for assigning war-room tasks to teams."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from app.business.war_room_tasks import _validate_assignee
from app.business.war_room_tasks import _validate_team_ids
from app.business.war_room_tasks import war_room_task_create
from app.business.war_room_tasks import war_room_task_update
from app.business.war_room_tasks import war_room_task_teams
from app.business.war_room_teams import war_room_team_find_by_handle
from app.models.errors import BusinessProcessingError


_TASKS = 'app.business.war_room_tasks'


class TestValidateTeamIds(TestCase):

    def test_none_is_empty(self):
        self.assertEqual(_validate_team_ids(1, None), [])

    def test_empty_list_skips_lookup(self):
        with patch(f'{_TASKS}.task_teams_db_room_team_ids') as lookup:
            self.assertEqual(_validate_team_ids(1, []), [])
        lookup.assert_not_called()

    def test_deduplicates_in_order(self):
        with patch(f'{_TASKS}.task_teams_db_room_team_ids', return_value={3, 7}):
            self.assertEqual(_validate_team_ids(1, [7, 3, 7]), [7, 3])

    def test_rejects_non_list(self):
        with self.assertRaises(BusinessProcessingError):
            _validate_team_ids(1, 3)

    def test_rejects_bool_and_non_positive(self):
        for bad in ([True], [0], [-2], ['3'], [1.5]):
            with self.subTest(bad=bad), self.assertRaises(BusinessProcessingError):
                _validate_team_ids(1, bad)

    def test_rejects_too_many(self):
        with self.assertRaises(BusinessProcessingError):
            _validate_team_ids(1, list(range(1, 60)))

    def test_rejects_team_of_another_room(self):
        with patch(f'{_TASKS}.task_teams_db_room_team_ids', return_value={3}):
            with self.assertRaises(BusinessProcessingError) as ctx:
                _validate_team_ids(1, [3, 9])
        self.assertIn('#9', ctx.exception.get_message())


class TestTaskTeams(TestCase):

    def test_groups_rows_by_task(self):
        rows = [
            SimpleNamespace(task_id=1, team_id=5, name='Blue', color='#00f'),
            SimpleNamespace(task_id=2, team_id=6, name='Red', color=None),
            SimpleNamespace(task_id=1, team_id=6, name='Red', color=None),
        ]
        with patch(f'{_TASKS}.task_teams_db_for_tasks', return_value=rows):
            result = war_room_task_teams([1, 2])
        self.assertEqual([t['team_id'] for t in result[1]], [5, 6])
        self.assertEqual(result[2], [{'team_id': 6, 'name': 'Red', 'color': None}])

    def test_no_tasks_no_query(self):
        with patch(f'{_TASKS}.task_teams_db_for_tasks') as lookup:
            self.assertEqual(war_room_task_teams([]), {})
        lookup.assert_not_called()


class TestFindTeamByHandle(TestCase):

    def _find(self, handle):
        teams = [SimpleNamespace(team_id=1, name='Blue Team'), SimpleNamespace(team_id=2, name='SOC')]
        with patch('app.business.war_room_teams.war_room_team_list', return_value=teams):
            return war_room_team_find_by_handle(4, handle)

    def test_case_insensitive(self):
        self.assertEqual(self._find('soc').team_id, 2)

    def test_dash_and_underscore_stand_for_spaces(self):
        self.assertEqual(self._find('blue-team').team_id, 1)
        self.assertEqual(self._find('Blue_Team').team_id, 1)

    def test_unknown_or_empty(self):
        self.assertIsNone(self._find('red'))
        self.assertIsNone(self._find(''))


class TestValidateAssignee(TestCase):

    def test_none_is_unassigned(self):
        with patch('app.business.war_room_decisions.war_room_decisions_is_participant') as check:
            self.assertIsNone(_validate_assignee(10, None))
        check.assert_not_called()

    def test_rejects_non_ids(self):
        for value in ('3', True, 0, -1, 2.5):
            with self.subTest(value=value):
                with self.assertRaisesRegex(BusinessProcessingError, 'assignee_id must be a user id'):
                    _validate_assignee(10, value)

    def test_participant_is_accepted(self):
        with patch('app.business.war_room_decisions.war_room_decisions_is_participant',
                   return_value=True) as check:
            self.assertEqual(3, _validate_assignee(10, 3))
        check.assert_called_once_with(10, 3)

    def test_unknown_and_non_member_users_get_the_same_error(self):
        with patch('app.business.war_room_decisions.war_room_decisions_is_participant', return_value=False):
            with self.assertRaises(BusinessProcessingError) as ctx:
                _validate_assignee(10, 99)
        self.assertEqual('Unknown or non-member user', ctx.exception.get_message())


class TestTaskAssigneeChecks(TestCase):

    def test_create_rejects_a_non_member_assignee_before_writing(self):
        with patch('app.business.war_room_decisions.war_room_decisions_is_participant', return_value=False), \
                patch(f'{_TASKS}.db') as db:
            with self.assertRaisesRegex(BusinessProcessingError, 'Unknown or non-member user'):
                war_room_task_create(10, 'Reimage WS-042', assignee_id=99, created_by_id=1)
        db.session.add.assert_not_called()

    def test_update_checks_only_a_changed_assignee(self):
        task = SimpleNamespace(task_id=4, assignee_id=99, title='t', description=None)
        with patch(f'{_TASKS}.war_room_task_get', return_value=task), \
                patch('app.business.war_room_decisions.war_room_decisions_is_participant',
                      return_value=False) as check, \
                patch(f'{_TASKS}.db') as db:
            with self.assertRaisesRegex(BusinessProcessingError, 'Unknown or non-member user'):
                war_room_task_update(10, 4, assignee_id=50)
            db.session.commit.assert_not_called()
            check.assert_called_once_with(10, 50)
            check.reset_mock()
            # Re-sending the current assignee (who may have left the room)
            # is not re-validated.
            with patch(f'{_TASKS}.track_activity'), \
                    patch(f'{_TASKS}.call_modules_hook', side_effect=lambda _name, data, **_kw: data), \
                    patch(f'{_TASKS}._notify_assigned_teams'):
                war_room_task_update(10, 4, assignee_id=99)
            check.assert_not_called()
