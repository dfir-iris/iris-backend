#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 3 of the License, or (at your option) any later version.

"""Integration tests for the SitRep auto-draft, cadence and publish-share."""

from unittest import TestCase

from iris import Iris

_PERMISSION_WAR_ROOMS_READ = 0x8000
_PERMISSION_WAR_ROOMS_WRITE = 0x10000
_WAR_ROOM_FULL_ACCESS = 0x4


class TestsRestWarRoomSitRepAutoDraft(TestCase):

    def setUp(self) -> None:
        self._subject = Iris()

    def tearDown(self):
        self._subject.clear_database()

    def _room(self, name='SitRep room'):
        return self._subject.create('/api/v2/war-rooms', {'name': name}).json()['war_room_id']

    def _attach(self, room_id, case_id):
        self._subject.create(f'/api/v2/war-rooms/{room_id}/cases', {'case_id': case_id})

    def _member(self, room_id):
        user = self._subject.create_dummy_user(
            permissions=_PERMISSION_WAR_ROOMS_READ | _PERMISSION_WAR_ROOMS_WRITE)
        self._subject.create(f'/api/v2/war-rooms/{room_id}/members',
                             {'user_id': user.get_identifier(), 'access_level': _WAR_ROOM_FULL_ACCESS})
        return user

    def _draft(self, room_id, title='SitRep #1'):
        return self._subject.create(f'/api/v2/war-rooms/{room_id}/sitreps',
                                    {'title': title, 'body_md': 'All good'}).json()['sitrep_id']

    def test_auto_draft_should_return_markdown_and_sections(self):
        room_id = self._room()
        case_id = self._subject.create_dummy_case()
        self._attach(room_id, case_id)

        response = self._subject.get(f'/api/v2/war-rooms/{room_id}/sitreps/auto-draft')

        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertIn('## Per-case status', body['body_md'])
        self.assertIn(f'| #{case_id} ', body['body_md'])
        self.assertEqual([case_id], [c['case_id'] for c in body['sections']['cases']])
        for key in ('changes', 'decisions', 'exceptions', 'next_actions'):
            self.assertIn(key, body['sections'])
        self.assertIsNotNone(body['since'])
        self.assertIsNotNone(body['generated_at'])

    def test_auto_draft_should_escape_pipes_in_case_names(self):
        room_id = self._room(name='Wave | 1')
        response = self._subject.get(f'/api/v2/war-rooms/{room_id}/sitreps/auto-draft')
        self.assertIn('Wave \\| 1', response.json()['body_md'])

    def test_auto_draft_should_ignore_cases_the_user_cannot_read(self):
        room_id = self._room()
        case_id = self._subject.create_dummy_case()
        self._attach(room_id, case_id)
        user = self._member(room_id)

        body = user.get(f'/api/v2/war-rooms/{room_id}/sitreps/auto-draft').json()

        self.assertEqual([], body['sections']['cases'])
        self.assertNotIn(f'#{case_id} ', body['body_md'])

    def test_auto_draft_should_list_open_war_room_tasks_as_next_actions(self):
        room_id = self._room()
        self._subject.create(f'/api/v2/war-rooms/{room_id}/tasks', {'title': 'Call the ISP'})

        body = self._subject.get(f'/api/v2/war-rooms/{room_id}/sitreps/auto-draft').json()

        self.assertIn('Call the ISP', [a['title'] for a in body['sections']['next_actions']])

    def test_cadence_default_should_be_empty(self):
        room_id = self._room()
        body = self._subject.get(f'/api/v2/war-rooms/{room_id}/sitreps/cadence').json()
        self.assertIsNone(body['cadence_minutes'])
        self.assertIsNone(body['next_due_at'])
        self.assertFalse(body['is_overdue'])

    def test_cadence_put_should_compute_next_due(self):
        room_id = self._room()
        response = self._subject.update(f'/api/v2/war-rooms/{room_id}/sitreps/cadence',
                                        {'cadence_minutes': 60, 'reminder_minutes': 10})
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual(60, body['cadence_minutes'])
        self.assertEqual(10, body['reminder_minutes'])
        self.assertIsNotNone(body['next_due_at'])
        room = self._subject.get(f'/api/v2/war-rooms/{room_id}').json()
        self.assertEqual(60, room['sitrep_cadence_minutes'])
        self.assertEqual(10, room['sitrep_reminder_minutes'])

    def test_cadence_put_should_validate(self):
        room_id = self._room()
        for body in ({'cadence_minutes': 5}, {'cadence_minutes': 20000}, {'cadence_minutes': '60'},
                     {'cadence_minutes': 60, 'reminder_minutes': 61}, {'reminder_minutes': 10}):
            response = self._subject.update(f'/api/v2/war-rooms/{room_id}/sitreps/cadence', body)
            self.assertEqual(400, response.status_code, body)

    def test_cadence_put_null_should_clear(self):
        room_id = self._room()
        self._subject.update(f'/api/v2/war-rooms/{room_id}/sitreps/cadence', {'cadence_minutes': 60})
        body = self._subject.update(f'/api/v2/war-rooms/{room_id}/sitreps/cadence',
                                    {'cadence_minutes': None}).json()
        self.assertIsNone(body['cadence_minutes'])
        self.assertIsNone(body['next_due_at'])

    def test_cadence_last_published_should_follow_publish(self):
        room_id = self._room()
        sitrep_id = self._draft(room_id)
        self._subject.create(f'/api/v2/war-rooms/{room_id}/sitreps/{sitrep_id}/publish', {})
        body = self._subject.get(f'/api/v2/war-rooms/{room_id}/sitreps/cadence').json()
        self.assertIsNotNone(body['last_published_at'])

    def test_publish_without_body_should_still_work(self):
        room_id = self._room()
        sitrep_id = self._draft(room_id)
        response = self._subject.create(f'/api/v2/war-rooms/{room_id}/sitreps/{sitrep_id}/publish', None)
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertTrue(body['published'])
        self.assertEqual([], body['shared'])

    def test_publish_should_copy_into_selected_cases(self):
        room_id = self._room()
        case_id = self._subject.create_dummy_case()
        self._attach(room_id, case_id)
        sitrep_id = self._draft(room_id)

        response = self._subject.create(f'/api/v2/war-rooms/{room_id}/sitreps/{sitrep_id}/publish',
                                        {'share_to_case_ids': [case_id]})

        self.assertEqual(200, response.status_code)
        shared = response.json()['shared']
        self.assertEqual(1, len(shared))
        self.assertEqual('copied', shared[0]['status'])
        note = self._subject.get(f'/api/v2/cases/{case_id}/notes/{shared[0]["note_id"]}')
        self.assertEqual(200, note.status_code)

    def test_publish_share_all_should_target_every_attached_case(self):
        room_id = self._room()
        case_ids = [self._subject.create_dummy_case(), self._subject.create_dummy_case()]
        for case_id in case_ids:
            self._attach(room_id, case_id)
        sitrep_id = self._draft(room_id)

        shared = self._subject.create(f'/api/v2/war-rooms/{room_id}/sitreps/{sitrep_id}/publish',
                                      {'share_to_case_ids': 'all'}).json()['shared']

        self.assertEqual(sorted(case_ids), sorted(row['case_id'] for row in shared))

    def test_publish_share_should_deny_unattached_or_unwritable_cases(self):
        room_id = self._room()
        attached = self._subject.create_dummy_case()
        self._attach(room_id, attached)
        unattached = self._subject.create_dummy_case()
        user = self._member(room_id)
        sitrep_id = self._draft(room_id)

        shared = user.create(f'/api/v2/war-rooms/{room_id}/sitreps/{sitrep_id}/publish',
                             {'share_to_case_ids': [attached, unattached]}).json()['shared']

        self.assertEqual(['denied', 'denied'], [row['status'] for row in shared])

    def test_publish_share_should_reject_invalid_targets_without_publishing(self):
        room_id = self._room()
        sitrep_id = self._draft(room_id)

        response = self._subject.create(f'/api/v2/war-rooms/{room_id}/sitreps/{sitrep_id}/publish',
                                        {'share_to_case_ids': ['x']})

        self.assertEqual(400, response.status_code)
        sitrep = self._subject.get(f'/api/v2/war-rooms/{room_id}/sitreps/{sitrep_id}').json()
        self.assertFalse(sitrep['published'])
