#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Regression tests for `flush_to_source` against concurrently deleted rows.

GlitchTip #237: `StaleDataError: UPDATE statement on table 'notes' expected
to update 1 row(s); 0 were matched.`, logged as `collab: flush failed for
note:209`.

`flush_to_source` runs on last-client-disconnect, arbitrarily long after the
doc was opened, and nothing in the codebase ever deletes the CollabDoc row —
so the source row it targets can be gone by the time it fires. None of the
models involved declares a `version_id_col`, so a rowcount of 0 does not mean
a version conflict: it means the row was deleted. Under READ COMMITTED the
SELECT that loads the row can still see it while a concurrent delete commits
before the UPDATE lands.

The old code assigned to the loaded instance and committed, which makes the
ORM assert it updated exactly one row. These tests pin the replacement: a
criteria-scoped UPDATE that reports a rowcount instead of raising.

Pure unit tests — every collaborator of `flush_to_source` is patched, so no
DB, Flask context or pycrdt is needed.
"""

from unittest import TestCase
from unittest.mock import MagicMock
from unittest.mock import patch

from app.business.collab import flush_to_source


NEW = 'flushed markdown'
OLD = 'stale markdown'


def _collab_row():
    """A CollabDoc whose y_state renders to NEW and whose cache is current.

    `content_md` already equals NEW so the first commit is skipped — that
    keeps each test focused on the source-column write.
    """
    row = MagicMock()
    row.y_state = b'\x00'
    row.content_md = NEW
    row.updated_by_id = 7
    return row


def _model_with_row(row, rowcount):
    """Stub a model whose `.first()` yields `row` and whose criteria-scoped
    UPDATE reports `rowcount` rows written."""
    model = MagicMock()
    model.query.filter_by.return_value.first.return_value = row
    model.query.filter_by.return_value.update.return_value = rowcount
    return model


def _note(content=OLD):
    note = MagicMock()
    note.note_content = content
    note.note_title = 'Investigation notes'
    note.note_case_id = 3532
    return note


class _FlushHarness:
    """Patches every collaborator of `flush_to_source` for one `kind`."""

    def __init__(self, model_name, model):
        self._patchers = [
            patch('app.business.collab.CollabDoc',
                  _model_with_row(_collab_row(), 1)),
            patch(f'app.business.collab.{model_name}', model),
            patch('app.business.collab.ydoc_update_to_markdown',
                  return_value=NEW),
            patch('app.business.collab.track_activity'),
            patch('app.business.collab.db'),
        ]

    def __enter__(self):
        started = [p.start() for p in self._patchers]
        self.track_activity = started[3]
        self.db = started[4]
        return self

    def __exit__(self, *exc_info):
        for p in self._patchers:
            p.stop()
        return False


class TestFlushToleratesDeletedNote(TestCase):
    """The row vanished between the SELECT and the UPDATE."""

    def test_deleted_note_does_not_raise(self):
        # This is the crash from GlitchTip #237: 0 rows matched.
        model = _model_with_row(_note(), 0)
        with _FlushHarness('Notes', model):
            flush_to_source('note:209')  # must not raise

    def test_deleted_note_does_not_log_activity(self):
        # Nothing was written, so claiming "updated note" in the audit log
        # would be a lie.
        model = _model_with_row(_note(), 0)
        with _FlushHarness('Notes', model) as h:
            flush_to_source('note:209')
        h.track_activity.assert_not_called()

    def test_missing_note_never_attempts_an_update(self):
        model = _model_with_row(None, 0)
        with _FlushHarness('Notes', model) as h:
            flush_to_source('note:209')
        model.query.filter_by.return_value.update.assert_not_called()
        h.track_activity.assert_not_called()


class TestFlushDoesNotUseTheOrmUnitOfWork(TestCase):
    """The load-bearing assertions: what actually caused the StaleDataError."""

    def test_does_not_assign_to_the_loaded_instance(self):
        # The old code did `note.note_content = new_content`, which makes the
        # ORM emit a single-row-asserted UPDATE on commit. Leaving the loaded
        # instance untouched is precisely what stops StaleDataError.
        note = _note()
        model = _model_with_row(note, 1)
        with _FlushHarness('Notes', model):
            flush_to_source('note:209')
        self.assertEqual(OLD, note.note_content)

    def test_writes_through_a_criteria_scoped_update(self):
        note = _note()
        model = _model_with_row(note, 1)
        with _FlushHarness('Notes', model):
            flush_to_source('note:209')
        model.query.filter_by.return_value.update.assert_called_once()
        args, kwargs = model.query.filter_by.return_value.update.call_args
        self.assertEqual({'note_content': NEW}, args[0])
        # Synchronising would re-load the instance we deliberately avoid.
        self.assertFalse(kwargs['synchronize_session'])

    def test_update_is_scoped_to_the_target_row(self):
        model = _model_with_row(_note(), 1)
        with _FlushHarness('Notes', model):
            flush_to_source('note:209')
        model.query.filter_by.assert_called_with(note_id=209)


class TestFlushStillWritesTheHappyPath(TestCase):
    """The fix must not break the case where the row is still there."""

    def test_surviving_note_is_written_and_logged(self):
        model = _model_with_row(_note(), 1)
        with _FlushHarness('Notes', model) as h:
            flush_to_source('note:209')
        h.db.session.commit.assert_called_once()
        h.track_activity.assert_called_once()
        self.assertIn('Investigation notes',
                      h.track_activity.call_args[0][0])

    def test_activity_is_attributed_to_the_collab_doc_author(self):
        # The socket disconnect has no Flask-Login user; `updated_by_id` on
        # the CollabDoc is the authoritative last writer.
        model = _model_with_row(_note(), 1)
        with _FlushHarness('Notes', model) as h:
            flush_to_source('note:209')
        self.assertEqual(7, h.track_activity.call_args[1]['user_id_override'])
        self.assertEqual(3532, h.track_activity.call_args[1]['caseid'])

    def test_unchanged_note_is_not_rewritten(self):
        model = _model_with_row(_note(content=NEW), 1)
        with _FlushHarness('Notes', model) as h:
            flush_to_source('note:209')
        model.query.filter_by.return_value.update.assert_not_called()
        h.track_activity.assert_not_called()


class _ExpiringNote:
    """A Notes row that mimics `expire_on_commit=True`.

    Once the commit fires the instance is expired, so the next attribute
    read would re-SELECT the row — and a concurrently deleted row raises
    ObjectDeletedError. Here that read is simply an error, which fails the
    test if `flush_to_source` defers its metadata reads until after the
    commit.
    """

    def __init__(self):
        self.note_content = OLD
        self._expired = False

    def expire(self):
        self._expired = True

    def _read(self, value):
        if self._expired:
            raise AssertionError('attribute read after commit expired it')
        return value

    @property
    def note_title(self):
        return self._read('Investigation notes')

    @property
    def note_case_id(self):
        return self._read(3532)


class TestActivityMetadataSurvivesTheCommit(TestCase):
    """`expire_on_commit` makes post-commit attribute access re-SELECT the
    row — which raises if it was deleted. The metadata must be read first."""

    def test_note_attributes_are_read_before_the_commit(self):
        note = _ExpiringNote()
        model = _model_with_row(note, 1)
        with _FlushHarness('Notes', model) as h:
            h.db.session.commit.side_effect = note.expire
            flush_to_source('note:209')  # must not raise
        h.track_activity.assert_called_once()
        self.assertIn('Investigation notes', h.track_activity.call_args[0][0])
        self.assertEqual(3532, h.track_activity.call_args[1]['caseid'])


class TestOtherDocKindsAreGuardedToo(TestCase):
    """All five kinds shared the same load-mutate-commit shape, so all five
    could raise. `note` is simply the one that fired in production."""

    def test_deleted_case_summary_does_not_raise(self):
        case = MagicMock()
        case.description = OLD
        case.case_id = 3532
        model = _model_with_row(case, 0)
        with _FlushHarness('Cases', model) as h:
            flush_to_source('case-summary:3532')
        h.track_activity.assert_not_called()

    def test_deleted_war_room_note_does_not_raise(self):
        wrn = MagicMock()
        wrn.content = OLD
        wrn.title = 'War room note'
        wrn.war_room_id = 4
        model = _model_with_row(wrn, 0)
        with _FlushHarness('WarRoomNote', model) as h:
            flush_to_source('war-room-note:11')
        h.track_activity.assert_not_called()

    def test_deleted_war_room_summary_does_not_raise(self):
        room = MagicMock()
        room.description = OLD
        room.war_room_id = 4
        model = _model_with_row(room, 0)
        with _FlushHarness('WarRoom', model) as h:
            flush_to_source('war-room-summary:4')
        h.track_activity.assert_not_called()

    def test_sitrep_update_re_checks_published_in_the_criteria(self):
        # Publishing races with the flush exactly as deletion does, so the
        # guard has to live in the UPDATE criteria and not only in the
        # in-Python check above it.
        sit = MagicMock()
        sit.body_md = OLD
        sit.published = False
        sit.title = 'Sitrep'
        sit.version = 2
        sit.war_room_id = 4
        model = _model_with_row(sit, 0)
        with _FlushHarness('WarRoomSitRep', model) as h:
            flush_to_source('sitrep:5')
        model.query.filter_by.assert_called_with(sitrep_id=5, published=False)
        h.track_activity.assert_not_called()

    def test_published_sitrep_is_never_written(self):
        sit = MagicMock()
        sit.body_md = OLD
        sit.published = True
        model = _model_with_row(sit, 1)
        with _FlushHarness('WarRoomSitRep', model) as h:
            flush_to_source('sitrep:5')
        model.query.filter_by.return_value.update.assert_not_called()
        h.track_activity.assert_not_called()
