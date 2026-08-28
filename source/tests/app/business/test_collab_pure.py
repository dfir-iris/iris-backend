#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for the pure helper `_parse_doc_name` in app.business.collab.

`_parse_doc_name` is entirely self-contained — no DB, no Flask context, no
socket.io — so it can be exercised without any infrastructure setup.

The other functions in that module (resolve_doc, ensure_snapshot,
apply_wire_update, flush_to_source) all touch DB or pycrdt and are covered
by integration tests elsewhere.
"""

from unittest import TestCase

from app.business.collab import DocResolutionError, _parse_doc_name


class TestParseDocNameShape(TestCase):
    """Validation of the structural constraints on doc_name."""

    def test_valid_note_returns_tuple(self):
        kind, obj_id = _parse_doc_name('note:42')
        self.assertEqual('note', kind)
        self.assertEqual(42, obj_id)

    def test_valid_case_summary(self):
        kind, obj_id = _parse_doc_name('case-summary:7')
        self.assertEqual('case-summary', kind)
        self.assertEqual(7, obj_id)

    def test_valid_war_room_note(self):
        kind, obj_id = _parse_doc_name('war-room-note:1')
        self.assertEqual('war-room-note', kind)
        self.assertEqual(1, obj_id)

    def test_valid_war_room_summary(self):
        kind, obj_id = _parse_doc_name('war-room-summary:99')
        self.assertEqual('war-room-summary', kind)
        self.assertEqual(99, obj_id)

    def test_valid_sitrep(self):
        kind, obj_id = _parse_doc_name('sitrep:5')
        self.assertEqual('sitrep', kind)
        self.assertEqual(5, obj_id)

    def test_id_is_int(self):
        _, obj_id = _parse_doc_name('note:123')
        self.assertIsInstance(obj_id, int)

    def test_large_id_parsed(self):
        _, obj_id = _parse_doc_name('note:999999')
        self.assertEqual(999999, obj_id)

    def test_id_one_is_valid(self):
        _, obj_id = _parse_doc_name('note:1')
        self.assertEqual(1, obj_id)


class TestParseDocNameRejectsNonString(TestCase):
    """Non-string inputs must raise DocResolutionError."""

    def test_none_raises(self):
        with self.assertRaises(DocResolutionError):
            _parse_doc_name(None)

    def test_integer_raises(self):
        with self.assertRaises(DocResolutionError):
            _parse_doc_name(42)

    def test_list_raises(self):
        with self.assertRaises(DocResolutionError):
            _parse_doc_name(['note', '1'])

    def test_dict_raises(self):
        with self.assertRaises(DocResolutionError):
            _parse_doc_name({'note': 1})


class TestParseDocNameRejectsMissingColon(TestCase):
    """Strings without a colon must raise DocResolutionError."""

    def test_plain_kind_raises(self):
        with self.assertRaises(DocResolutionError):
            _parse_doc_name('note')

    def test_empty_string_raises(self):
        with self.assertRaises(DocResolutionError):
            _parse_doc_name('')

    def test_slash_separated_raises(self):
        with self.assertRaises(DocResolutionError):
            _parse_doc_name('note/42')


class TestParseDocNameRejectsUnknownKind(TestCase):
    """Known-kind whitelist is enforced."""

    def test_unknown_kind_raises(self):
        with self.assertRaises(DocResolutionError):
            _parse_doc_name('unknown:1')

    def test_partial_kind_raises(self):
        with self.assertRaises(DocResolutionError):
            _parse_doc_name('not:1')

    def test_empty_kind_raises(self):
        with self.assertRaises(DocResolutionError):
            _parse_doc_name(':1')

    def test_case_sensitive_kind(self):
        # Kind check is case-sensitive — 'NOTE' is not in the whitelist.
        with self.assertRaises(DocResolutionError):
            _parse_doc_name('NOTE:1')

    def test_arbitrary_string_raises(self):
        with self.assertRaises(DocResolutionError):
            _parse_doc_name('report:10')


class TestParseDocNameRejectsNonIntegerId(TestCase):
    """ID portion must be an integer string."""

    def test_float_id_raises(self):
        with self.assertRaises(DocResolutionError):
            _parse_doc_name('note:1.5')

    def test_alpha_id_raises(self):
        with self.assertRaises(DocResolutionError):
            _parse_doc_name('note:abc')

    def test_empty_id_raises(self):
        with self.assertRaises(DocResolutionError):
            _parse_doc_name('note:')

    def test_negative_id_parses(self):
        # int('-1') succeeds, so negative ids are technically accepted by
        # the parser — the DB lookup will simply find nothing.
        _, obj_id = _parse_doc_name('note:-1')
        self.assertEqual(-1, obj_id)

    def test_whitespace_id_raises(self):
        with self.assertRaises(DocResolutionError):
            _parse_doc_name('note: ')


class TestParseDocNameColonInId(TestCase):
    """Extra colons in the string: `partition` is used so only the FIRST
    colon splits kind from id; everything after goes into the id segment."""

    def test_extra_colon_makes_id_invalid(self):
        # 'note:1:2' → kind='note', raw_id='1:2' → int('1:2') raises
        with self.assertRaises(DocResolutionError):
            _parse_doc_name('note:1:2')
