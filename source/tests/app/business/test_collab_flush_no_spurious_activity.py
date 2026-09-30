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

"""Merely opening a collaborative doc must not log an update.

Every visit to a case summary logged "updated case summary" for the
visitor. The Y.Doc is seeded from the raw source column, and the
parse/render round trip is not the identity (terminal newline, `*` → `-`
bullets, soft → hard breaks, escaping…). The flush on disconnect compared
the render byte-for-byte against the column, saw a difference, rewrote the
column and logged it — with no editor recorded, `track_activity` fell back
to the visitor.

The renderer is real here; only the DB-facing collaborators are patched.
"""

from unittest import TestCase
from unittest.mock import MagicMock
from unittest.mock import patch

from app.business.collab import flush_to_source
from app.iris_engine.collab.render import markdown_to_ydoc_update
from app.iris_engine.collab.render import normalize_markdown


# Raw markdown as alert merges, case templates and the v1 API write it:
# none of it survives the round trip byte-for-byte.
_RAW_SUMMARY = (
    '# Summary\n'
    '* first finding\n'
    '* second finding\n'
    '\n'
    'line one\nline two   \n'
    '\n'
    '| a | b |\n'
    '|---|---|\n'
    '| 1 | 2 |'
)


def _collab_row(y_state, updated_by_id):
    row = MagicMock()
    row.y_state = y_state
    row.content_md = None
    row.updated_by_id = updated_by_id
    return row


def _model_with_row(row):
    model = MagicMock()
    model.query.filter_by.return_value.first.return_value = row
    model.query.filter_by.return_value.update.return_value = 1
    return model


def _case(description):
    case = MagicMock()
    case.case_id = 17
    case.description = description
    return case


def _flush_summary(description, y_state, updated_by_id):
    """Flush `case-summary:17`; return (Cases model, track_activity mock)."""
    cases = _model_with_row(_case(description))
    with patch('app.business.collab.CollabDoc',
               _model_with_row(_collab_row(y_state, updated_by_id))), \
            patch('app.business.collab.Cases', cases), \
            patch('app.business.collab.track_activity') as track, \
            patch('app.business.collab.db'):
        flush_to_source('case-summary:17')
    return cases, track


class TestRoundTripIsNotTheIdentity(TestCase):
    """Pins the premise: without it the byte comparison would be enough."""

    def test_raw_markdown_changes_on_round_trip(self):
        self.assertNotEqual(_RAW_SUMMARY, normalize_markdown(_RAW_SUMMARY))

    def test_normalization_is_idempotent(self):
        once = normalize_markdown(_RAW_SUMMARY)
        self.assertEqual(once, normalize_markdown(once))

    def test_none_normalizes_to_empty(self):
        self.assertEqual('', normalize_markdown(None))


class TestVisitDoesNotLogAnUpdate(TestCase):

    def test_seeded_but_unedited_summary_is_not_rewritten_or_logged(self):
        seeded = markdown_to_ydoc_update(_RAW_SUMMARY)
        cases, track = _flush_summary(_RAW_SUMMARY, seeded, None)
        cases.query.filter_by.return_value.update.assert_not_called()
        track.assert_not_called()

    def test_formatting_difference_is_ignored_even_with_a_past_editor(self):
        # Someone edited once, long ago; since then an alert merge appended
        # raw markdown to both the column and the Y.Doc. A later visit must
        # not re-attribute an update to that past editor.
        seeded = markdown_to_ydoc_update(_RAW_SUMMARY)
        cases, track = _flush_summary(_RAW_SUMMARY, seeded, 42)
        cases.query.filter_by.return_value.update.assert_not_called()
        track.assert_not_called()


class TestRealEditIsStillFlushed(TestCase):

    def test_edit_is_written_and_attributed_to_the_editor(self):
        edited = markdown_to_ydoc_update(_RAW_SUMMARY + '\n\nNew paragraph')
        cases, track = _flush_summary(_RAW_SUMMARY, edited, 42)
        cases.query.filter_by.return_value.update.assert_called_once()
        written = cases.query.filter_by.return_value.update.call_args[0][0]
        self.assertIn('New paragraph', written['description'])
        track.assert_called_once()
        self.assertEqual('updated case summary', track.call_args[0][0])
        self.assertEqual(42, track.call_args[1]['user_id_override'])

    def test_change_without_a_recorded_editor_is_written_but_not_logged(self):
        # With no editor on record `track_activity` would blame whoever
        # triggered the flush. Keep the content, drop the misattribution.
        changed = markdown_to_ydoc_update('Something else entirely')
        cases, track = _flush_summary(_RAW_SUMMARY, changed, None)
        cases.query.filter_by.return_value.update.assert_called_once()
        track.assert_not_called()
