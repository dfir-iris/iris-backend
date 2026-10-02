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

"""Unit tests for the server-side Y.Doc read / replace helpers.

`CollabDoc` and the session are mocked; the rendering is real.
"""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock
from unittest.mock import patch

from app.iris_engine.collab import sync
from app.iris_engine.collab.render import markdown_to_ydoc_update
from app.iris_engine.collab.render import ydoc_update_to_markdown


def _collab_doc(row):
    model = MagicMock()
    model.query.filter_by.return_value.first.return_value = row
    return model


class TestCollabReplaceMarkdown(TestCase):

    def _run(self, row, md='# New\n\nBody.\n'):
        db = MagicMock()
        broadcast = MagicMock()
        with patch.object(sync, 'CollabDoc', _collab_doc(row)), \
                patch.object(sync, 'db', db), \
                patch.object(sync, '_broadcast_state', broadcast):
            sync.collab_replace_markdown('note:1', md)
        return db, broadcast

    def test_never_opened_document_is_left_to_the_source_column(self):
        db, broadcast = self._run(None)
        db.session.commit.assert_not_called()
        broadcast.assert_not_called()

    def test_stored_state_is_replaced_and_committed(self):
        row = SimpleNamespace(y_state=markdown_to_ydoc_update('# Old\n'),
                              content_md='# Old\n', last_flushed_at=None)
        db, _ = self._run(row)
        rendered = ydoc_update_to_markdown(row.y_state)
        self.assertNotIn('Old', rendered)
        self.assertIn('# New', rendered)
        self.assertEqual(rendered, row.content_md)
        self.assertIsNotNone(row.last_flushed_at)
        db.session.commit.assert_called_once()

    def test_connected_editors_get_the_new_state(self):
        row = SimpleNamespace(y_state=markdown_to_ydoc_update('# Old\n'),
                              content_md='', last_flushed_at=None)
        _, broadcast = self._run(row)
        broadcast.assert_called_once_with('note:1', row.y_state)

    def test_a_failure_is_swallowed_and_rolled_back(self):
        row = SimpleNamespace(y_state=b'not a yjs update', content_md='',
                              last_flushed_at=None)
        db, broadcast = self._run(row)
        db.session.rollback.assert_called_once()
        broadcast.assert_not_called()


class TestCollabAppendMarkdown(TestCase):

    def _run(self, row):
        db = MagicMock()
        broadcast = MagicMock()
        with patch.object(sync, 'CollabDoc', _collab_doc(row)), \
                patch.object(sync, 'db', db), \
                patch.object(sync, '_broadcast_state', broadcast):
            sync.collab_append_markdown('case-summary:1', 'Escalated.\n')
        return db, broadcast

    def test_connected_editors_get_the_appended_state(self):
        row = SimpleNamespace(y_state=markdown_to_ydoc_update('# Summary\n'),
                              content_md='', last_flushed_at=None)
        _, broadcast = self._run(row)
        broadcast.assert_called_once_with('case-summary:1', row.y_state)
        self.assertIn('Escalated.', ydoc_update_to_markdown(row.y_state))

    def test_never_opened_document_is_not_broadcast(self):
        _, broadcast = self._run(None)
        broadcast.assert_not_called()

    def test_failed_append_is_not_broadcast(self):
        row = SimpleNamespace(y_state=b'not a yjs update', content_md='',
                              last_flushed_at=None)
        db, broadcast = self._run(row)
        db.session.rollback.assert_called_once()
        broadcast.assert_not_called()


class TestBroadcastState(TestCase):

    def test_state_is_emitted_into_the_collab_room(self):
        socket_io = MagicMock()
        with patch('app.socket_io', socket_io):
            sync._broadcast_state('note:1', b'\x00\x01')
        socket_io.emit.assert_called_once_with(
            'sync', {'doc': 'note:1', 'update': 'AAE=', 'origin': None},
            to='note:1', namespace='/collab')

    def test_emit_failure_is_swallowed(self):
        socket_io = MagicMock()
        socket_io.emit.side_effect = RuntimeError('queue down')
        with patch('app.socket_io', socket_io):
            sync._broadcast_state('note:1', b'\x00')


class TestCollabCurrentMarkdown(TestCase):

    def _run(self, row, fallback='column'):
        with patch.object(sync, 'CollabDoc', _collab_doc(row)):
            return sync.collab_current_markdown('note:1', fallback)

    def test_never_opened_document_returns_the_column(self):
        self.assertEqual('column', self._run(None))

    def test_opened_document_returns_the_ydoc_content(self):
        row = SimpleNamespace(y_state=markdown_to_ydoc_update('Typed live.\n'))
        self.assertEqual('Typed live.\n', self._run(row))

    def test_unrenderable_state_falls_back_to_the_column(self):
        row = SimpleNamespace(y_state=b'not a yjs update')
        self.assertEqual('column', self._run(row))
