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

"""Unit tests for war-room note sharing: the reconcile diff (datamgmt
mocked), the copy delivery, the case-side read-only guards and the
collab ACL of mirrors. No DB."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock
from unittest.mock import patch

from app.business import war_room_note_shares as shares_biz
from app.business.collab import resolve_doc
from app.business.notes import notes_check_writable
from app.business.notes import notes_create
from app.business.notes import notes_delete
from app.business.notes import notes_update
from app.business.notes_directories import NOTES_MIRROR_READ_ONLY_MESSAGE
from app.business.notes_directories import notes_directories_create
from app.business.notes_directories import notes_directories_delete
from app.business.notes_directories import notes_directories_update
from app.business.war_room_note_shares import war_room_note_shares_compute_desired
from app.business.war_room_note_shares import war_room_note_shares_copy_to_cases
from app.business.war_room_note_shares import war_room_note_shares_parse_create
from app.business.war_room_note_shares import war_room_note_shares_reconcile
from app.business.war_room_note_shares import war_room_note_shares_reconcile_safe
from app.models.errors import BusinessProcessingError
from app.models.errors import UnhandledBusinessError
from app.schema.marshables import CaseNoteSchema


_MODULE = 'app.business.war_room_note_shares'
WAR_ROOM_ID = 9


def _share(share_id, note_id=None, folder_id=None, scope='all', delivery='mirror'):
    return SimpleNamespace(share_id=share_id, war_room_id=WAR_ROOM_ID, note_id=note_id,
                           folder_id=folder_id, scope=scope, delivery=delivery,
                           include_future=False)


def _room_note(note_id, title='Title', content='Body', folder_id=None):
    return SimpleNamespace(note_id=note_id, title=title, content=content, folder_id=folder_id)


def _folder(folder_id, parent_id=None):
    return SimpleNamespace(id=folder_id, parent_id=parent_id, name=f'F{folder_id}')


def _mirror(note_id, case_id, source_note_id, title='Title', content='Body', directory_id=100):
    return SimpleNamespace(note_id=note_id, note_case_id=case_id,
                           mirror_source_note_id=source_note_id,
                           mirror_war_room_id=WAR_ROOM_ID,
                           note_title=title, note_content=content, directory_id=directory_id)


def _directory(directory_id, case_id, name='War room · Room'):
    return SimpleNamespace(id=directory_id, case_id=case_id, name=name,
                           mirror_war_room_id=WAR_ROOM_ID)


class TestComputeDesired(TestCase):

    def test_note_share_scope_all_targets_every_attached_case(self):
        desired = war_room_note_shares_compute_desired(
            [_share(1, note_id=5)], {}, [10, 11], {5: _room_note(5)}, [])
        self.assertEqual({(10, 5), (11, 5)}, desired)

    def test_scope_cases_is_listed_intersect_attached(self):
        desired = war_room_note_shares_compute_desired(
            [_share(1, note_id=5, scope='cases')], {1: [10, 99]}, [10, 11],
            {5: _room_note(5)}, [])
        self.assertEqual({(10, 5)}, desired)

    def test_folder_share_covers_the_whole_subtree(self):
        notes = {
            5: _room_note(5, folder_id=1),
            6: _room_note(6, folder_id=2),
            7: _room_note(7, folder_id=3),
            8: _room_note(8, folder_id=None),
        }
        folders = [_folder(1), _folder(2, parent_id=1), _folder(3)]
        desired = war_room_note_shares_compute_desired(
            [_share(1, folder_id=1)], {}, [10], notes, folders)
        self.assertEqual({(10, 5), (10, 6)}, desired)

    def test_copy_shares_and_missing_sources_are_ignored(self):
        desired = war_room_note_shares_compute_desired(
            [_share(1, note_id=5, delivery='copy'), _share(2, note_id=404)],
            {}, [10], {5: _room_note(5)}, [])
        self.assertEqual(set(), desired)


class _ReconcileHarness:
    """Patches every datamgmt collaborator of the reconciler."""

    def __init__(self, shares=(), mirrors=(), directories=(), attached=(), notes=(),
                 folders=(), share_case_ids=None, empty_directories=True):
        self.created_directories = []
        self._next_directory_id = 500

        def _create_directory(case_id, war_room_id, name):
            directory = _directory(self._next_directory_id, case_id, name)
            self._next_directory_id += 1
            self.created_directories.append(directory)
            return directory

        self.mocks = {
            'lock_war_room_note_shares': MagicMock(),
            'get_war_room': MagicMock(return_value=SimpleNamespace(war_room_id=WAR_ROOM_ID, name='Room')),
            'list_shares': MagicMock(return_value=list(shares)),
            'list_mirror_notes': MagicMock(return_value=list(mirrors)),
            'list_mirror_directories': MagicMock(return_value=list(directories)),
            'list_attached_case_ids': MagicMock(return_value=list(attached)),
            'list_share_case_ids': MagicMock(return_value=share_case_ids or {}),
            'list_room_notes': MagicMock(return_value=list(notes)),
            'list_room_folders': MagicMock(return_value=list(folders)),
            'get_user_login': MagicMock(return_value='analyst'),
            'create_mirror_directory': MagicMock(side_effect=_create_directory),
            'create_mirror_note': MagicMock(),
            'update_mirror_note': MagicMock(),
            'delete_mirror_note': MagicMock(),
            'directory_is_empty': MagicMock(return_value=empty_directories),
            'delete_directory': MagicMock(),
            'bump_notes_state': MagicMock(),
            'track_activity': MagicMock(),
            'db': MagicMock(),
        }
        self._patchers = [patch(f'{_MODULE}.{name}', mock) for name, mock in self.mocks.items()]

    def __enter__(self):
        for patcher in self._patchers:
            patcher.start()
        return self

    def __exit__(self, *exc):
        for patcher in self._patchers:
            patcher.stop()
        return False


class TestReconcile(TestCase):

    def test_creates_missing_mirror_and_locked_directory(self):
        with _ReconcileHarness(shares=[_share(1, note_id=5)], attached=[10],
                               notes=[_room_note(5, title='Plan', content='md')]) as h:
            summary = war_room_note_shares_reconcile(WAR_ROOM_ID, actor_id=3)
        self.assertEqual(1, summary['created'])
        h.mocks['create_mirror_directory'].assert_called_once_with(10, WAR_ROOM_ID, 'War room · Room')
        h.mocks['create_mirror_note'].assert_called_once_with(
            10, 500, WAR_ROOM_ID, 5, 'Plan', 'md', 3, 'analyst')
        h.mocks['db'].session.commit.assert_called_once()
        h.mocks['track_activity'].assert_called_once()
        self.assertEqual(10, h.mocks['track_activity'].call_args.kwargs['caseid'])

    def test_truncates_titles_to_the_case_note_column(self):
        with _ReconcileHarness(shares=[_share(1, note_id=5)], attached=[10],
                               notes=[_room_note(5, title='x' * 400)]) as h:
            war_room_note_shares_reconcile(WAR_ROOM_ID, actor_id=3)
        self.assertEqual(155, len(h.mocks['create_mirror_note'].call_args.args[4]))

    def test_updates_stale_mirror_only(self):
        mirrors = [_mirror(70, 10, 5, title='Old'), _mirror(71, 10, 6)]
        with _ReconcileHarness(shares=[_share(1, note_id=5), _share(2, note_id=6)], attached=[10],
                               notes=[_room_note(5, title='New'), _room_note(6)],
                               mirrors=mirrors, directories=[_directory(100, 10)]) as h:
            summary = war_room_note_shares_reconcile(WAR_ROOM_ID, actor_id=3)
        self.assertEqual(1, summary['updated'])
        h.mocks['update_mirror_note'].assert_called_once_with(mirrors[0], 'New', 'Body', 100, 3, 'analyst')
        h.mocks['create_mirror_note'].assert_not_called()
        h.mocks['create_mirror_directory'].assert_not_called()

    def test_is_idempotent_when_in_sync(self):
        with _ReconcileHarness(shares=[_share(1, note_id=5)], attached=[10],
                               notes=[_room_note(5)], mirrors=[_mirror(70, 10, 5)],
                               directories=[_directory(100, 10)]) as h:
            summary = war_room_note_shares_reconcile(WAR_ROOM_ID, actor_id=3)
        self.assertEqual({'created': 0, 'updated': 0, 'deleted': 0, 'directories_deleted': 0}, summary)
        for name in ('create_mirror_note', 'update_mirror_note', 'delete_mirror_note',
                     'delete_directory', 'track_activity', 'bump_notes_state'):
            h.mocks[name].assert_not_called()

    def test_deletes_undesired_orphan_and_duplicate_mirrors_then_empty_directory(self):
        mirrors = [
            _mirror(70, 10, 5),       # kept
            _mirror(71, 10, 5),       # duplicate
            _mirror(72, 10, None),    # orphan (source deleted)
            _mirror(73, 11, 5),       # case detached
        ]
        directories = [_directory(100, 10), _directory(101, 11)]
        with _ReconcileHarness(shares=[_share(1, note_id=5)], attached=[10],
                               notes=[_room_note(5)], mirrors=mirrors,
                               directories=directories) as h:
            summary = war_room_note_shares_reconcile(WAR_ROOM_ID, actor_id=3)
        deleted = [call.args[0].note_id for call in h.mocks['delete_mirror_note'].call_args_list]
        self.assertEqual([71, 72, 73], deleted)
        self.assertEqual(3, summary['deleted'])
        h.mocks['delete_directory'].assert_called_once_with(directories[1])

    def test_unshare_removes_every_mirror_and_directory(self):
        with _ReconcileHarness(shares=[], mirrors=[_mirror(70, 10, 5)],
                               directories=[_directory(100, 10)], attached=[10],
                               notes=[_room_note(5)]) as h:
            war_room_note_shares_reconcile(WAR_ROOM_ID, actor_id=3)
        h.mocks['delete_mirror_note'].assert_called_once()
        h.mocks['delete_directory'].assert_called_once()

    def test_renames_directory_when_war_room_renamed(self):
        directory = _directory(100, 10, name='War room · Old name')
        with _ReconcileHarness(shares=[_share(1, note_id=5)], attached=[10],
                               notes=[_room_note(5)], mirrors=[_mirror(70, 10, 5)],
                               directories=[directory]):
            war_room_note_shares_reconcile(WAR_ROOM_ID, actor_id=3)
        self.assertEqual('War room · Room', directory.name)

    def test_nothing_shared_short_circuits(self):
        with _ReconcileHarness() as h:
            war_room_note_shares_reconcile(WAR_ROOM_ID, actor_id=3)
        h.mocks['list_room_notes'].assert_not_called()
        h.mocks['lock_war_room_note_shares'].assert_called_once_with(WAR_ROOM_ID)

    def test_safe_wrapper_never_raises_and_rolls_back(self):
        with patch(f'{_MODULE}.war_room_note_shares_reconcile', side_effect=RuntimeError('db down')), \
                patch(f'{_MODULE}.db') as db:
            self.assertFalse(war_room_note_shares_reconcile_safe(WAR_ROOM_ID))
        db.session.rollback.assert_called_once()


class TestParseCreate(TestCase):

    def _parse(self, raw, attached=(10, 11)):
        with patch(f'{_MODULE}.get_room_note', return_value=_room_note(5)), \
                patch(f'{_MODULE}.get_room_folder', return_value=_folder(1)), \
                patch(f'{_MODULE}.list_attached_case_ids', return_value=list(attached)):
            return war_room_note_shares_parse_create(WAR_ROOM_ID, raw)

    def test_requires_exactly_one_source(self):
        with self.assertRaises(BusinessProcessingError):
            self._parse({'note_id': 5, 'folder_id': 1, 'scope': 'all'})
        with self.assertRaises(BusinessProcessingError):
            self._parse({'scope': 'all'})

    def test_rejects_bool_ids_and_unattached_cases(self):
        with self.assertRaises(BusinessProcessingError):
            self._parse({'note_id': 5, 'scope': 'cases', 'case_ids': [True]})
        with self.assertRaises(BusinessProcessingError):
            self._parse({'note_id': 5, 'scope': 'cases', 'case_ids': [12]})

    def test_copy_ignores_include_future(self):
        parsed = self._parse({'note_id': 5, 'scope': 'cases', 'case_ids': [10, 10],
                              'include_future': True, 'delivery': 'copy'})
        self.assertEqual([10], parsed['case_ids'])
        self.assertFalse(parsed['include_future'])


class TestCopyToCases(TestCase):

    def setUp(self):
        self._patchers = [
            patch(f'{_MODULE}.get_war_room', return_value=SimpleNamespace(war_room_id=WAR_ROOM_ID, name='Room')),
            patch(f'{_MODULE}.list_attached_case_ids', return_value=[10, 11]),
            patch(f'{_MODULE}.find_copy_directory', return_value=None),
            patch(f'{_MODULE}.get_default_note_custom_attributes', return_value={}),
            patch(f'{_MODULE}.db'),
        ]
        for patcher in self._patchers:
            patcher.start()
        self.addCleanup(lambda: [patcher.stop() for patcher in self._patchers])

    def test_copies_into_attached_cases_and_reports_errors(self):
        created_directories = []

        def _create_directory(directory):
            directory.id = 300 + len(created_directories)
            created_directories.append(directory)

        def _create_note(note, case_id):
            if case_id == 11:
                raise BusinessProcessingError('boom')
            note.note_id = 900
            return note

        with patch(f'{_MODULE}.notes_directories_create', side_effect=_create_directory), \
                patch(f'{_MODULE}.notes_create', side_effect=_create_note) as create_note:
            results = war_room_note_shares_copy_to_cases(
                WAR_ROOM_ID, 'S' * 300, '# SitRep', [10, 11, 12, 10], user_id=3)

        self.assertEqual([
            {'case_id': 10, 'status': 'copied', 'note_id': 900},
            {'case_id': 11, 'status': 'error', 'message': 'Unable to copy the note into this case'},
            {'case_id': 12, 'status': 'error', 'message': 'Case is not attached to this war room'},
        ], results)
        copied = create_note.call_args_list[0].args[0]
        self.assertEqual(155, len(copied.note_title))
        self.assertEqual('# SitRep', copied.note_content)
        self.assertIsNone(copied.mirror_source_note_id)
        self.assertEqual('War room · Room', created_directories[0].name)
        self.assertIsNone(created_directories[0].mirror_war_room_id)

    def test_reuses_existing_copy_directory(self):
        with patch(f'{_MODULE}.find_copy_directory', return_value=SimpleNamespace(id=42)), \
                patch(f'{_MODULE}.notes_directories_create') as create_directory, \
                patch(f'{_MODULE}.notes_create', return_value=SimpleNamespace(note_id=1)) as create_note:
            war_room_note_shares_copy_to_cases(WAR_ROOM_ID, 'T', 'md', [10], user_id=3)
        create_directory.assert_not_called()
        self.assertEqual(42, create_note.call_args.args[0].directory_id)

    def test_all_resolves_to_attached_cases(self):
        with patch(f'{_MODULE}.notes_directories_create', side_effect=lambda d: setattr(d, 'id', 1)), \
                patch(f'{_MODULE}.notes_create', side_effect=lambda _note, case_id: SimpleNamespace(note_id=case_id)):
            results = war_room_note_shares_copy_to_cases(WAR_ROOM_ID, 'T', 'md', 'all', user_id=3)
        self.assertEqual([10, 11], [result['case_id'] for result in results])


def _case_note(mirror=False, directory_id=1):
    return SimpleNamespace(note_id=70, note_case_id=10, note_title='t', note_content='c',
                           directory_id=directory_id,
                           mirror_source_note_id=5 if mirror else None,
                           mirror_war_room_id=WAR_ROOM_ID if mirror else None)


class TestCaseSideGuards(TestCase):

    def setUp(self):
        db_patcher = patch('app.business.notes.db')
        directories_db_patcher = patch('app.business.notes_directories.db')
        db_patcher.start()
        directories_db_patcher.start()
        self.addCleanup(db_patcher.stop)
        self.addCleanup(directories_db_patcher.stop)

    def test_mirror_note_is_not_writable(self):
        with self.assertRaises(BusinessProcessingError) as ctx:
            notes_check_writable(_case_note(mirror=True))
        self.assertEqual(NOTES_MIRROR_READ_ONLY_MESSAGE, ctx.exception.get_message())
        notes_check_writable(_case_note())

    def test_update_of_mirror_is_a_business_error_not_unhandled(self):
        with patch('app.business.notes.update_note_revision') as update_revision:
            with self.assertRaises(BusinessProcessingError) as ctx:
                notes_update(SimpleNamespace(id=1), _case_note(mirror=True))
        self.assertNotIsInstance(ctx.exception, UnhandledBusinessError)
        update_revision.assert_not_called()

    def test_moving_a_note_into_a_locked_directory_is_refused(self):
        locked = SimpleNamespace(id=100, mirror_war_room_id=WAR_ROOM_ID)
        with patch('app.business.notes_directories.get_directory', return_value=locked), \
                patch('app.business.notes.update_note_revision') as update_revision:
            with self.assertRaises(BusinessProcessingError):
                notes_update(SimpleNamespace(id=1), _case_note(directory_id=100))
        update_revision.assert_not_called()

    def test_create_into_a_locked_directory_is_refused(self):
        locked = SimpleNamespace(id=100, mirror_war_room_id=WAR_ROOM_ID)
        with patch('app.business.notes_directories.get_directory', return_value=locked):
            with self.assertRaises(BusinessProcessingError) as ctx:
                notes_create(_case_note(directory_id=100), 10)
        self.assertEqual(NOTES_MIRROR_READ_ONLY_MESSAGE, ctx.exception.get_message())

    def test_delete_of_mirror_is_refused(self):
        with patch('app.business.notes.delete_note') as delete_note:
            with self.assertRaises(BusinessProcessingError):
                notes_delete(_case_note(mirror=True))
        delete_note.assert_not_called()

    def test_locked_directory_cannot_be_updated_deleted_or_nested_into(self):
        locked = SimpleNamespace(id=100, case_id=10, name='War room · Room', parent_id=None,
                                 mirror_war_room_id=WAR_ROOM_ID)
        with patch('app.business.notes_directories.delete_directory') as delete_directory:
            with self.assertRaises(BusinessProcessingError):
                notes_directories_delete(locked)
            delete_directory.assert_not_called()
        with self.assertRaises(BusinessProcessingError):
            notes_directories_update(locked)
        child = SimpleNamespace(id=None, case_id=10, name='x', parent_id=100, mirror_war_room_id=None)
        with patch('app.business.notes_directories.get_directory', return_value=locked):
            with self.assertRaises(BusinessProcessingError):
                notes_directories_create(child)


class TestCollabMirrorAcl(TestCase):

    def _resolve(self, note):
        with patch('app.business.collab.Notes') as notes_model, \
                patch('app.business.collab.ac_fast_check_user_has_case_access', return_value=1):
            notes_model.query.filter_by.return_value.first.return_value = note
            return resolve_doc('note:70', 3)

    def test_mirror_is_read_only_even_with_full_access(self):
        resolved = self._resolve(_case_note(mirror=True))
        self.assertTrue(resolved['can_read'])
        self.assertFalse(resolved['can_write'])

    def test_plain_note_stays_writable(self):
        self.assertTrue(self._resolve(_case_note())['can_write'])


class TestCaseNoteSchemaMirror(TestCase):

    def test_plain_note_has_no_mirror(self):
        self.assertIsNone(CaseNoteSchema._get_mirror(_case_note()))

    def test_mirror_note_dumps_its_origin(self):
        with patch('app.schema.marshables.db') as db:
            db.session.get.return_value = SimpleNamespace(name='Room')
            mirror = CaseNoteSchema._get_mirror(_case_note(mirror=True))
        self.assertEqual({'war_room_id': WAR_ROOM_ID, 'war_room_name': 'Room',
                          'source_note_id': 5, 'read_only': True}, mirror)


class TestTriggers(TestCase):

    def test_attach_extends_future_shares_then_reconciles(self):
        with patch(f'{_MODULE}.append_case_to_future_shares', return_value=1) as append, \
                patch(f'{_MODULE}.db') as db, \
                patch(f'{_MODULE}.war_room_note_shares_reconcile') as reconcile:
            shares_biz.war_room_note_shares_on_case_attached(WAR_ROOM_ID, 10, actor_id=3)
        append.assert_called_once_with(WAR_ROOM_ID, 10)
        db.session.commit.assert_called()
        reconcile.assert_called_once_with(WAR_ROOM_ID, actor_id=3)

    def test_detach_never_raises(self):
        with patch(f'{_MODULE}.remove_case_from_shares', side_effect=RuntimeError('x')), \
                patch(f'{_MODULE}.db'), \
                patch(f'{_MODULE}.war_room_note_shares_reconcile', side_effect=RuntimeError('y')):
            self.assertFalse(shares_biz.war_room_note_shares_on_case_detached(WAR_ROOM_ID, 10))
