#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 3 of the License, or (at your option) any later version.

"""Integration tests for sharing war-room notes with attached cases."""

from unittest import TestCase

from iris import IRIS_CASE_ACCESS_LEVEL_READ_ONLY
from iris import Iris

_PERMISSION_WAR_ROOMS_READ = 0x8000
_PERMISSION_WAR_ROOMS_WRITE = 0x10000
_WAR_ROOM_FULL_ACCESS = 0x4


class TestsRestWarRoomNoteShares(TestCase):

    def setUp(self) -> None:
        self._subject = Iris()

    def tearDown(self):
        self._subject.clear_database()

    def _room(self):
        return self._subject.create('/api/v2/war-rooms', {'name': 'Share room'}).json()['war_room_id']

    def _attach(self, room_id, case_id):
        self._subject.create(f'/api/v2/war-rooms/{room_id}/cases', {'case_id': case_id})

    def _folder(self, room_id, name='Playbooks'):
        return self._subject.create(f'/api/v2/war-rooms/{room_id}/notes-folders', {'name': name}).json()['id']

    def _note(self, room_id, title='Containment', content='Isolate hosts', folder_id=None):
        body = {'title': title, 'content': content}
        if folder_id is not None:
            body['folder_id'] = folder_id
        return self._subject.create(f'/api/v2/war-rooms/{room_id}/notes', body).json()['note_id']

    def _share(self, room_id, body, actor=None):
        actor = actor or self._subject
        return actor.create(f'/api/v2/war-rooms/{room_id}/note-shares', body)

    def _member(self, room_id):
        user = self._subject.create_dummy_user(permissions=_PERMISSION_WAR_ROOMS_READ | _PERMISSION_WAR_ROOMS_WRITE)
        self._subject.create(f'/api/v2/war-rooms/{room_id}/members',
                             {'user_id': user.get_identifier(), 'access_level': _WAR_ROOM_FULL_ACCESS})
        return user

    def _case_notes(self, case_id):
        return self._subject.get(f'/api/v2/cases/{case_id}/notes').json()

    def _mirrors(self, case_id, source_note_id):
        return [n for n in self._case_notes(case_id) if n.get('mirror_source_note_id') == source_note_id]

    def _setup_shared_note(self, delivery='mirror'):
        room_id = self._room()
        case_id = self._subject.create_dummy_case()
        self._attach(room_id, case_id)
        note_id = self._note(room_id)
        response = self._share(room_id, {'note_id': note_id, 'scope': 'cases', 'case_ids': [case_id],
                                         'delivery': delivery})
        return room_id, case_id, note_id, response

    def test_share_should_create_mirror_in_locked_directory(self):
        room_id, case_id, note_id, response = self._setup_shared_note()

        self.assertEqual(201, response.status_code)
        mirrors = self._mirrors(case_id, note_id)
        self.assertEqual(1, len(mirrors))
        mirror = mirrors[0]
        self.assertEqual('Containment', mirror['note_title'])
        self.assertEqual(room_id, mirror['mirror']['war_room_id'])
        self.assertTrue(mirror['mirror']['read_only'])
        directory = self._subject.get(f'/api/v2/cases/{case_id}/notes-directories/{mirror["directory_id"]}').json()
        self.assertEqual(room_id, directory['mirror_war_room_id'])

    def test_source_update_should_propagate_to_mirror(self):
        room_id, case_id, note_id, _ = self._setup_shared_note()

        self._subject.patch(f'/api/v2/war-rooms/{room_id}/notes/{note_id}',
                            {'title': 'Containment v2', 'content': 'Isolate and reimage'})

        mirror = self._mirrors(case_id, note_id)[0]
        self.assertEqual('Containment v2', mirror['note_title'])
        self.assertEqual('Isolate and reimage', mirror['note_content'])

    def test_case_side_update_of_mirror_should_return_400(self):
        _, case_id, note_id, _ = self._setup_shared_note()
        mirror = self._mirrors(case_id, note_id)[0]

        response = self._subject.update(f'/api/v2/cases/{case_id}/notes/{mirror["note_id"]}',
                                        {'note_title': 'hijack', 'note_content': 'x'})

        self.assertEqual(400, response.status_code)
        self.assertEqual('This note is mirrored from a war room and is read-only', response.json()['message'])

    def test_case_side_delete_of_mirror_should_return_400(self):
        _, case_id, note_id, _ = self._setup_shared_note()
        mirror = self._mirrors(case_id, note_id)[0]

        response = self._subject.delete(f'/api/v2/cases/{case_id}/notes/{mirror["note_id"]}')

        self.assertEqual(400, response.status_code)
        self.assertEqual(1, len(self._mirrors(case_id, note_id)))

    def test_case_side_delete_of_mirror_directory_should_return_400(self):
        _, case_id, note_id, _ = self._setup_shared_note()
        mirror = self._mirrors(case_id, note_id)[0]

        response = self._subject.delete(f'/api/v2/cases/{case_id}/notes-directories/{mirror["directory_id"]}')

        self.assertEqual(400, response.status_code)

    def test_unshare_should_remove_mirror(self):
        room_id, case_id, note_id, response = self._setup_shared_note()
        share_id = response.json()['share_id']

        delete_response = self._subject.delete(f'/api/v2/war-rooms/{room_id}/note-shares/{share_id}')

        self.assertEqual(204, delete_response.status_code)
        self.assertEqual([], self._mirrors(case_id, note_id))

    def test_folder_share_should_include_notes_added_later(self):
        room_id = self._room()
        case_id = self._subject.create_dummy_case()
        self._attach(room_id, case_id)
        folder_id = self._folder(room_id)
        self._note(room_id, title='First', folder_id=folder_id)
        self._share(room_id, {'folder_id': folder_id, 'scope': 'all', 'include_future': True})

        later_note_id = self._note(room_id, title='Later', folder_id=folder_id)

        self.assertEqual(1, len(self._mirrors(case_id, later_note_id)))

    def test_case_attached_later_should_receive_mirror_when_include_future(self):
        room_id = self._room()
        note_id = self._note(room_id)
        self._share(room_id, {'note_id': note_id, 'scope': 'all', 'include_future': True})

        case_id = self._subject.create_dummy_case()
        self._attach(room_id, case_id)

        self.assertEqual(1, len(self._mirrors(case_id, note_id)))

    def test_detach_case_should_remove_mirror(self):
        room_id, case_id, note_id, _ = self._setup_shared_note()

        self._subject.delete(f'/api/v2/war-rooms/{room_id}/cases/{case_id}')

        self.assertEqual([], self._mirrors(case_id, note_id))

    def test_copy_should_create_a_normal_editable_note(self):
        _, case_id, note_id, response = self._setup_shared_note(delivery='copy')

        self.assertEqual(201, response.status_code)
        copies = [n for n in self._case_notes(case_id) if n['note_title'] == 'Containment']
        self.assertEqual(1, len(copies))
        copy = copies[0]
        self.assertIsNone(copy['mirror'])
        update = self._subject.update(f'/api/v2/cases/{case_id}/notes/{copy["note_id"]}',
                                      {'note_title': 'Edited', 'note_content': 'mine now'})
        self.assertEqual(200, update.status_code)

    def test_share_should_be_refused_without_full_access_on_target_case(self):
        room_id = self._room()
        case_id = self._subject.create_dummy_case()
        self._attach(room_id, case_id)
        note_id = self._note(room_id)
        user = self._member(room_id)
        self._subject.grant_case_access(user, case_id, IRIS_CASE_ACCESS_LEVEL_READ_ONLY)

        response = self._share(room_id, {'note_id': note_id, 'scope': 'cases', 'case_ids': [case_id]}, actor=user)

        self.assertEqual(403, response.status_code)
        self.assertEqual([], self._mirrors(case_id, note_id))

    def test_share_all_should_return_400_without_full_access_on_an_attached_case(self):
        room_id = self._room()
        case_id = self._subject.create_dummy_case()
        self._attach(room_id, case_id)
        note_id = self._note(room_id)
        user = self._member(room_id)

        response = self._share(room_id, {'note_id': note_id, 'scope': 'all'}, actor=user)

        self.assertEqual(400, response.status_code)

    def test_case_side_create_note_into_mirror_directory_should_return_400(self):
        _, case_id, note_id, _ = self._setup_shared_note()
        mirror = self._mirrors(case_id, note_id)[0]

        response = self._subject.create(f'/api/v2/cases/{case_id}/notes',
                                        {'directory_id': mirror['directory_id'], 'note_title': 'sneaky'})

        self.assertEqual(400, response.status_code)

    def test_case_side_create_subdirectory_in_mirror_directory_should_return_400(self):
        _, case_id, note_id, _ = self._setup_shared_note()
        mirror = self._mirrors(case_id, note_id)[0]

        response = self._subject.create(f'/api/v2/cases/{case_id}/notes-directories',
                                        {'name': 'nested', 'parent_id': mirror['directory_id']})

        self.assertEqual(400, response.status_code)

    def test_case_side_restore_revision_of_mirror_should_return_400(self):
        _, case_id, note_id, _ = self._setup_shared_note()
        mirror = self._mirrors(case_id, note_id)[0]

        response = self._subject.create(
            f'/api/v2/cases/{case_id}/notes/{mirror["note_id"]}/revisions/1/restore', {})

        self.assertEqual(400, response.status_code)

    def test_legacy_update_of_mirror_should_be_refused(self):
        _, case_id, note_id, _ = self._setup_shared_note()
        mirror = self._mirrors(case_id, note_id)[0]

        response = self._subject.create(f'/case/notes/update/{mirror["note_id"]}',
                                        {'note_title': 'hijack', 'note_content': 'x'},
                                        query_parameters={'cid': case_id})

        self.assertEqual(400, response.status_code)
        self.assertEqual('Containment', self._mirrors(case_id, note_id)[0]['note_title'])

    def test_patch_share_to_another_case_should_move_mirror(self):
        room_id, case_id, note_id, response = self._setup_shared_note()
        other_case_id = self._subject.create_dummy_case()
        self._attach(room_id, other_case_id)
        share_id = response.json()['share_id']

        patch = self._subject.patch(f'/api/v2/war-rooms/{room_id}/note-shares/{share_id}',
                                    {'case_ids': [other_case_id]})

        self.assertEqual(200, patch.status_code)
        self.assertEqual([], self._mirrors(case_id, note_id))
        self.assertEqual(1, len(self._mirrors(other_case_id, note_id)))

    def test_list_shares_should_report_synced_target(self):
        room_id, case_id, note_id, _ = self._setup_shared_note()

        shares = self._subject.get(f'/api/v2/war-rooms/{room_id}/note-shares',
                                   query_parameters={'note_id': note_id}).json()

        self.assertEqual(1, len(shares))
        target = shares[0]['targets'][0]
        self.assertEqual(case_id, target['case_id'])
        self.assertTrue(target['accessible'])
        self.assertEqual('synced', target['status'])

    def test_list_shares_should_hide_names_of_unreadable_cases(self):
        room_id, case_id, note_id, _ = self._setup_shared_note()
        user = self._member(room_id)

        shares = user.get(f'/api/v2/war-rooms/{room_id}/note-shares').json()

        target = shares[0]['targets'][0]
        self.assertEqual({'case_id': case_id, 'accessible': False, 'status': 'synced'}, target)

    def test_preview_should_list_source_notes_and_targets(self):
        room_id = self._room()
        case_id = self._subject.create_dummy_case()
        self._attach(room_id, case_id)
        note_id = self._note(room_id)

        preview = self._subject.get(f'/api/v2/war-rooms/{room_id}/note-shares/preview',
                                    query_parameters={'note_id': note_id, 'scope': 'all'})

        self.assertEqual(200, preview.status_code)
        body = preview.json()
        self.assertEqual([note_id], [note['note_id'] for note in body['notes']])
        self.assertEqual([case_id], [target['case_id'] for target in body['targets']])

    def test_resync_should_recreate_missing_mirror_state(self):
        room_id, case_id, note_id, response = self._setup_shared_note()
        share_id = response.json()['share_id']

        resync = self._subject.create(f'/api/v2/war-rooms/{room_id}/note-shares/{share_id}/resync', {})

        self.assertEqual(200, resync.status_code)
        self.assertEqual(1, len(self._mirrors(case_id, note_id)))
