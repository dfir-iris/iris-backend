#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 3 of the License, or (at your option) any later version.

"""Integration tests for the war-room task fan-out into attached cases."""

from unittest import TestCase

from iris import ADMINISTRATOR_USER_IDENTIFIER
from iris import IRIS_CASE_ACCESS_LEVEL_READ_ONLY
from iris import Iris

_PERMISSION_WAR_ROOMS_READ = 0x8000
_PERMISSION_WAR_ROOMS_WRITE = 0x10000
_WAR_ROOM_FULL_ACCESS = 0x4


class TestsRestWarRoomFanOut(TestCase):

    def setUp(self) -> None:
        self._subject = Iris()

    def tearDown(self):
        self._subject.clear_database()

    def _room(self):
        return self._subject.create('/api/v2/war-rooms', {'name': 'Fan-out room'}).json()['war_room_id']

    def _attach(self, room_id, case_id):
        self._subject.create(f'/api/v2/war-rooms/{room_id}/cases', {'case_id': case_id})

    def _task(self, room_id, **kwargs):
        body = {'title': 'Reset krbtgt', 'description': 'Twice, 10h apart', 'tags': 'ad,urgent'}
        body.update(kwargs)
        return self._subject.create(f'/api/v2/war-rooms/{room_id}/tasks', body).json()['task_id']

    def _fan_out(self, room_id, task_id, body, actor=None):
        actor = actor or self._subject
        return actor.create(f'/api/v2/war-rooms/{room_id}/tasks/{task_id}/fan-out', body)

    def _member(self, room_id, permissions=_PERMISSION_WAR_ROOMS_READ | _PERMISSION_WAR_ROOMS_WRITE):
        user = self._subject.create_dummy_user(permissions=permissions)
        self._subject.create(f'/api/v2/war-rooms/{room_id}/members',
                             {'user_id': user.get_identifier(), 'access_level': _WAR_ROOM_FULL_ACCESS})
        return user

    def test_fan_out_should_create_one_case_task_per_case(self):
        room_id = self._room()
        case_ids = [self._subject.create_dummy_case(), self._subject.create_dummy_case()]
        for case_id in case_ids:
            self._attach(room_id, case_id)
        task_id = self._task(room_id)

        response = self._fan_out(room_id, task_id, {'case_ids': case_ids})

        self.assertEqual(200, response.status_code)
        results = response.json()['results']
        self.assertEqual(['created', 'created'], [r['status'] for r in results])
        self.assertEqual(case_ids, [r['case_id'] for r in results])

    def test_fan_out_case_task_should_carry_title_tags_back_reference_and_owner(self):
        room_id = self._room()
        case_id = self._subject.create_dummy_case()
        self._attach(room_id, case_id)
        task_id = self._task(room_id)

        result = self._fan_out(room_id, task_id, {'case_ids': [case_id]}).json()['results'][0]
        case_task = self._subject.get(f'/api/v2/cases/{case_id}/tasks/{result["case_task_id"]}').json()

        self.assertEqual('Reset krbtgt', case_task['task_title'])
        self.assertTrue(case_task['task_tags'].startswith('war-room'))
        self.assertIn('urgent', case_task['task_tags'])
        self.assertIn(f'(task #{task_id})', case_task['task_description'])
        self.assertEqual([ADMINISTRATOR_USER_IDENTIFIER], case_task['task_assignees_id'])

    def test_fan_out_twice_should_report_exists(self):
        room_id = self._room()
        case_id = self._subject.create_dummy_case()
        self._attach(room_id, case_id)
        task_id = self._task(room_id)
        self._fan_out(room_id, task_id, {'case_ids': [case_id]})

        results = self._fan_out(room_id, task_id, {'case_ids': [case_id]}).json()['results']

        self.assertEqual('exists', results[0]['status'])

    def test_fan_out_to_detached_case_should_be_denied(self):
        room_id = self._room()
        case_id = self._subject.create_dummy_case()
        task_id = self._task(room_id)

        results = self._fan_out(room_id, task_id, {'case_ids': [case_id]}).json()['results']

        self.assertEqual('denied', results[0]['status'])

    def test_fan_out_without_full_case_access_should_be_denied(self):
        room_id = self._room()
        case_id = self._subject.create_dummy_case()
        self._attach(room_id, case_id)
        task_id = self._task(room_id)
        user = self._member(room_id)
        self._subject.grant_case_access(user, case_id, IRIS_CASE_ACCESS_LEVEL_READ_ONLY)

        response = self._fan_out(room_id, task_id, {'case_ids': [case_id]}, actor=user)

        self.assertEqual(200, response.status_code)
        self.assertEqual('denied', response.json()['results'][0]['status'])

    def test_fan_out_should_reject_invalid_case_ids(self):
        room_id = self._room()
        task_id = self._task(room_id)
        for body in ({'case_ids': []}, {'case_ids': ['1']}, {'case_ids': [True]}, {}):
            response = self._fan_out(room_id, task_id, body)
            self.assertEqual(400, response.status_code, body)

    def test_fan_out_should_reject_unknown_status(self):
        room_id = self._room()
        case_id = self._subject.create_dummy_case()
        self._attach(room_id, case_id)
        task_id = self._task(room_id)

        response = self._fan_out(room_id, task_id, {'case_ids': [case_id], 'status_id': 999999})

        self.assertEqual(400, response.status_code)

    def test_fan_out_unknown_task_should_return_404(self):
        room_id = self._room()
        response = self._fan_out(room_id, 999999999, {'case_ids': [1]})
        self.assertEqual(404, response.status_code)

    def test_fan_out_should_require_war_room_write(self):
        room_id = self._room()
        case_id = self._subject.create_dummy_case()
        self._attach(room_id, case_id)
        task_id = self._task(room_id)
        reader = self._member(room_id, permissions=_PERMISSION_WAR_ROOMS_READ)

        response = self._fan_out(room_id, task_id, {'case_ids': [case_id]}, actor=reader)

        self.assertEqual(403, response.status_code)

    def test_status_should_list_linked_cases(self):
        room_id = self._room()
        case_id = self._subject.create_dummy_case()
        self._attach(room_id, case_id)
        task_id = self._task(room_id)
        self._fan_out(room_id, task_id, {'case_ids': [case_id]})

        rows = self._subject.get(f'/api/v2/war-rooms/{room_id}/tasks/{task_id}/fan-out').json()

        self.assertEqual(1, len(rows))
        self.assertTrue(rows[0]['accessible'])
        self.assertEqual(case_id, rows[0]['case_id'])
        self.assertFalse(rows[0]['done'])
        self.assertIsNotNone(rows[0]['case_task_id'])

    def test_status_should_hide_details_of_unreadable_cases(self):
        room_id = self._room()
        case_id = self._subject.create_dummy_case()
        self._attach(room_id, case_id)
        task_id = self._task(room_id)
        self._fan_out(room_id, task_id, {'case_ids': [case_id]})
        user = self._member(room_id)

        rows = user.get(f'/api/v2/war-rooms/{room_id}/tasks/{task_id}/fan-out').json()

        self.assertEqual([case_id], [r['case_id'] for r in rows])
        self.assertFalse(rows[0]['accessible'])
        self.assertNotIn('case_name', rows[0])
        self.assertNotIn('task_title', rows[0])

    def test_summary_should_count_links_per_task(self):
        room_id = self._room()
        case_id = self._subject.create_dummy_case()
        self._attach(room_id, case_id)
        task_id = self._task(room_id)
        self._fan_out(room_id, task_id, {'case_ids': [case_id]})

        summary = self._subject.get(f'/api/v2/war-rooms/{room_id}/tasks/fan-out-summary').json()

        self.assertEqual({'total': 1, 'done': 0, 'accessible_total': 1}, summary[str(task_id)])

    def test_unlink_should_return_204_and_keep_the_case_task(self):
        room_id = self._room()
        case_id = self._subject.create_dummy_case()
        self._attach(room_id, case_id)
        task_id = self._task(room_id)
        case_task_id = self._fan_out(room_id, task_id,
                                     {'case_ids': [case_id]}).json()['results'][0]['case_task_id']

        response = self._subject.delete(f'/api/v2/war-rooms/{room_id}/tasks/{task_id}/fan-out/{case_id}')

        self.assertEqual(204, response.status_code)
        rows = self._subject.get(f'/api/v2/war-rooms/{room_id}/tasks/{task_id}/fan-out').json()
        self.assertEqual([], rows)
        self.assertEqual(200, self._subject.get(f'/api/v2/cases/{case_id}/tasks/{case_task_id}').status_code)

    def test_unlink_missing_link_should_return_404(self):
        room_id = self._room()
        task_id = self._task(room_id)
        response = self._subject.delete(f'/api/v2/war-rooms/{room_id}/tasks/{task_id}/fan-out/999999')
        self.assertEqual(404, response.status_code)
