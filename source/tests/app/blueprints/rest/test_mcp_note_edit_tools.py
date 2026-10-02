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

"""Unit tests for Yuki's note-edit tools (case notes and war-room notes).

Once a note has been opened in the editor its Y.Doc is authoritative, so
an edit has to reach the Y.Doc and must start from the Y.Doc's content.
The business layer and the collab helpers are mocked: these tests pin
the wiring between them, not the rendering (see test_render.py).
"""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock
from unittest.mock import patch

from app.blueprints.rest.v2.mcp import classification
from app.blueprints.rest.v2.mcp.dispatch import MCPError
from app.blueprints.rest.v2.mcp.tools import notes as notes_tools
from app.blueprints.rest.v2.mcp.tools import war_rooms as war_rooms_tools
from app.models.errors import ObjectNotFoundError


_WR = 'app.blueprints.rest.v2.mcp.tools.war_rooms'
_NOTES = 'app.blueprints.rest.v2.mcp.tools.notes'


def _user():
    user = MagicMock()
    user._get_current_object.return_value = SimpleNamespace(id=5)
    return user


def _war_room_note(**kwargs):
    fields = {'note_id': 8, 'title': 'IOC sweep', 'content': 'column body',
              'created_by_id': 5, 'folder_id': None, 'created_at': None}
    fields.update(kwargs)
    return SimpleNamespace(**fields)


class TestWarRoomNotesUpdate(TestCase):

    def _run(self, args, note=None):
        update = MagicMock(return_value=note or _war_room_note())
        replace = MagicMock()
        with patch(f'{_WR}.iris_current_user', _user()), \
                patch(f'{_WR}.war_room_notes_biz.war_room_note_update', update), \
                patch(f'{_WR}.collab_replace_markdown', replace):
            result = war_rooms_tools.iris_war_room_notes_update({'war_room_id': 3, **args})
        return result, update, replace

    def test_content_edit_goes_through_the_business_layer(self):
        _, update, _ = self._run({'note_id': 8, 'content': '# Clean'})
        update.assert_called_once_with(war_room_id=3, note_id=8, title=None,
                                       content='# Clean', updated_by_id=5)

    def test_content_edit_is_pushed_into_the_ydoc(self):
        _, _, replace = self._run({'note_id': 8, 'content': '# Clean'})
        replace.assert_called_once_with('war-room-note:8', '# Clean')

    def test_title_only_edit_leaves_the_ydoc_alone(self):
        _, update, replace = self._run({'note_id': 8, 'title': 'Renamed'})
        self.assertEqual('Renamed', update.call_args.kwargs['title'])
        replace.assert_not_called()

    def test_empty_content_is_an_edit_too(self):
        _, _, replace = self._run({'note_id': 8, 'content': ''})
        replace.assert_called_once_with('war-room-note:8', '')

    def test_nothing_to_update_is_rejected(self):
        with self.assertRaises(MCPError):
            self._run({'note_id': 8})

    def test_unknown_note_is_reported(self):
        with patch(f'{_WR}.iris_current_user', _user()), \
                patch(f'{_WR}.war_room_notes_biz.war_room_note_update',
                      MagicMock(side_effect=ObjectNotFoundError())), \
                patch(f'{_WR}.collab_replace_markdown') as replace:
            with self.assertRaises(MCPError):
                war_rooms_tools.iris_war_room_notes_update(
                    {'war_room_id': 3, 'note_id': 99, 'content': 'x'})
        replace.assert_not_called()


class TestWarRoomNotesListReadsLiveContent(TestCase):

    def test_list_returns_the_ydoc_content(self):
        live = MagicMock(return_value='typed but not flushed')
        with patch(f'{_WR}.war_room_notes_biz.war_room_note_list',
                   MagicMock(return_value=[_war_room_note()])), \
                patch(f'{_WR}.collab_current_markdown', live):
            result = war_rooms_tools.iris_war_room_notes_list({'war_room_id': 3})
        live.assert_called_once_with('war-room-note:8', 'column body')
        self.assertEqual('typed but not flushed', result['notes'][0]['content'])

    def test_created_note_serialises_the_column(self):
        # Only reads go through the Y.Doc; the serializer default stays
        # on the row so create/update don't pay a render.
        self.assertEqual('column body', war_rooms_tools._note(_war_room_note())['content'])


class TestCaseNotesUpdate(TestCase):

    def _run(self, payload):
        note = SimpleNamespace(note_id=42, note_content='new body')
        schema = MagicMock()
        schema.dump.return_value = {'note_id': 42}
        replace = MagicMock()
        with patch(f'{_NOTES}._get_note_in_case', MagicMock(return_value=note)), \
                patch(f'{_NOTES}._note_schema', schema), \
                patch(f'{_NOTES}.notes_update', MagicMock(return_value=note)), \
                patch(f'{_NOTES}.collab_replace_markdown', replace):
            notes_tools.iris_case_notes_update(
                {'case_identifier': 1, 'note_identifier': 42, 'payload': payload})
        return replace

    def test_content_edit_is_pushed_into_the_ydoc(self):
        replace = self._run({'note_content': 'new body'})
        replace.assert_called_once_with('note:42', 'new body')

    def test_title_only_edit_leaves_the_ydoc_alone(self):
        self._run({'note_title': 'Renamed'}).assert_not_called()


class TestCaseNotesGetReadsLiveContent(TestCase):

    def test_get_returns_the_ydoc_content(self):
        note = SimpleNamespace(note_id=42)
        schema = MagicMock()
        schema.dump.return_value = {'note_id': 42, 'note_content': 'column body'}
        live = MagicMock(return_value='typed but not flushed')
        with patch(f'{_NOTES}._get_note_in_case', MagicMock(return_value=note)), \
                patch(f'{_NOTES}._note_schema', schema), \
                patch(f'{_NOTES}.collab_current_markdown', live):
            result = notes_tools.iris_case_notes_get(
                {'case_identifier': 1, 'note_identifier': 42})
        live.assert_called_once_with('note:42', 'column body')
        self.assertEqual('typed but not flushed', result['note_content'])


class TestNoteEditToolClassification(TestCase):

    def test_war_room_note_update_needs_approval(self):
        self.assertTrue(classification.is_write('iris_war_room_notes_update'))
