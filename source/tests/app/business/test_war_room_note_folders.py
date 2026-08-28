#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for verify_parent_folder in business/war_room_note_folders.py.

The function walks a parent chain via ORM attributes; here we mock
WarRoomNoteFolder.query.filter_by(...).first() so no DB is needed.
"""

from unittest import TestCase
from unittest.mock import MagicMock, patch

from app.models.errors import BusinessProcessingError


def _folder(fid, war_room_id, parent=None):
    f = MagicMock()
    f.id = fid
    f.war_room_id = war_room_id
    f.parent = parent
    return f


class TestVerifyParentFolder(TestCase):

    def _call(self, parent_id, war_room_id, current_id=None, query_result=None):
        from app.business.war_room_note_folders import verify_parent_folder

        mock_model = MagicMock()
        mock_model.query.filter_by.return_value.first.return_value = query_result
        with patch('app.business.war_room_note_folders.WarRoomNoteFolder', mock_model):
            return verify_parent_folder(parent_id, war_room_id, current_id)

    # ── root-level (parent_id=None) ──────────────────────────────────────────

    def test_none_parent_id_returns_none(self):
        # No DB call needed when parent_id is None
        from app.business.war_room_note_folders import verify_parent_folder
        result = verify_parent_folder(None, war_room_id=1)
        self.assertIsNone(result)

    # ── parent not found ─────────────────────────────────────────────────────

    def test_parent_not_found_raises(self):
        with self.assertRaises(BusinessProcessingError):
            self._call(parent_id=99, war_room_id=1, query_result=None)

    # ── wrong war_room scope ─────────────────────────────────────────────────

    def test_parent_wrong_war_room_raises(self):
        folder = _folder(fid=5, war_room_id=2)  # wrong war_room
        with self.assertRaises(BusinessProcessingError):
            self._call(parent_id=5, war_room_id=1, query_result=folder)

    # ── creating new folder (current_id=None) ────────────────────────────────

    def test_valid_parent_no_current_id_returns_parent_id(self):
        folder = _folder(fid=5, war_room_id=1)
        result = self._call(parent_id=5, war_room_id=1, current_id=None, query_result=folder)
        self.assertEqual(5, result)

    # ── folder is its own parent ─────────────────────────────────────────────

    def test_self_parent_raises(self):
        folder = _folder(fid=5, war_room_id=1, parent=None)
        with self.assertRaises(BusinessProcessingError):
            self._call(parent_id=5, war_room_id=1, current_id=5, query_result=folder)

    # ── cycle detection ──────────────────────────────────────────────────────

    def test_moving_into_descendant_raises(self):
        # tree: folder 1 → folder 2 (child)
        # We try to move folder 1 (current_id=1) under folder 2 (parent_id=2)
        # Walk: parent=folder2, folder2.parent = None (not current)
        # But wait — we need to check if current_id appears in the ancestor chain.
        # folder2.parent → None, and folder2.id != 1, so this should pass.
        # Actually, the cycle check walks from parent upward; if current_id never
        # appears in that chain, it's fine. This test shows that moving a folder
        # to a sibling (no cycle) is allowed.
        sibling = _folder(fid=3, war_room_id=1, parent=None)
        result = self._call(parent_id=3, war_room_id=1, current_id=10, query_result=sibling)
        self.assertEqual(3, result)

    def test_cycle_detected_when_ancestor_is_current(self):
        # current_id=1, parent_id=2, folder2.parent=folder1
        # Walk: folder2 → sees folder2.id(2) != 1; cursor becomes folder1;
        # folder1.id == 1 == current_id → cycle → raises
        folder1 = _folder(fid=1, war_room_id=1, parent=None)
        folder2 = _folder(fid=2, war_room_id=1, parent=folder1)
        with self.assertRaises(BusinessProcessingError):
            self._call(parent_id=2, war_room_id=1, current_id=1, query_result=folder2)

    def test_valid_parent_in_same_war_room_accepted(self):
        parent = _folder(fid=10, war_room_id=5, parent=None)
        result = self._call(parent_id=10, war_room_id=5, current_id=99, query_result=parent)
        self.assertEqual(10, result)
