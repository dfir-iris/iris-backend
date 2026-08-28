#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for pure validators in business/war_room_chat.py.

_validate_kind, _validate_body, _validate_thread_title, and
_virtual_activity_row are all pure — no DB, no Flask context needed.
"""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock

from app.business.war_room_chat import (
    _validate_body,
    _validate_kind,
    _validate_thread_title,
    _virtual_activity_row,
    _BODY_MAX_LEN,
)
from app.models.errors import BusinessProcessingError


# ---------------------------------------------------------------------------
# _validate_kind
# ---------------------------------------------------------------------------

class TestValidateKind(TestCase):

    def test_none_defaults_to_message(self):
        self.assertEqual('message', _validate_kind(None))

    def test_message_accepted(self):
        self.assertEqual('message', _validate_kind('message'))

    def test_system_accepted(self):
        self.assertEqual('system', _validate_kind('system'))

    def test_note_accepted(self):
        self.assertEqual('note', _validate_kind('note'))

    def test_invalid_kind_raises(self):
        with self.assertRaises(BusinessProcessingError):
            _validate_kind('invalid_kind')

    def test_integer_kind_raises(self):
        with self.assertRaises(BusinessProcessingError):
            _validate_kind(42)

    def test_empty_string_raises(self):
        with self.assertRaises(BusinessProcessingError):
            _validate_kind('')


# ---------------------------------------------------------------------------
# _validate_body
# ---------------------------------------------------------------------------

class TestValidateBody(TestCase):

    def test_valid_body_returned(self):
        result = _validate_body('Hello world', 'message')
        self.assertEqual('Hello world', result)

    def test_none_body_for_system_message_is_ok(self):
        result = _validate_body(None, 'system')
        self.assertIsNone(result)

    def test_none_body_for_message_kind_raises(self):
        with self.assertRaises(BusinessProcessingError):
            _validate_body(None, 'message')

    def test_whitespace_only_message_raises(self):
        with self.assertRaises(BusinessProcessingError):
            _validate_body('   ', 'message')

    def test_non_string_body_raises(self):
        with self.assertRaises(BusinessProcessingError):
            _validate_body(42, 'message')

    def test_body_at_max_len_accepted(self):
        body = 'a' * _BODY_MAX_LEN
        result = _validate_body(body, 'message')
        self.assertEqual(body, result)

    def test_body_over_max_len_raises(self):
        body = 'a' * (_BODY_MAX_LEN + 1)
        with self.assertRaises(BusinessProcessingError):
            _validate_body(body, 'message')

    def test_whitespace_only_system_body_is_ok(self):
        # system kind doesn't require content
        result = _validate_body('  ', 'system')
        self.assertEqual('  ', result)


# ---------------------------------------------------------------------------
# _validate_thread_title
# ---------------------------------------------------------------------------

class TestValidateThreadTitle(TestCase):

    def test_none_returns_none(self):
        self.assertIsNone(_validate_thread_title(None))

    def test_empty_string_returns_none(self):
        self.assertIsNone(_validate_thread_title(''))

    def test_whitespace_only_returns_none(self):
        self.assertIsNone(_validate_thread_title('   '))

    def test_valid_title_stripped_and_returned(self):
        result = _validate_thread_title('  My Thread  ')
        self.assertEqual('My Thread', result)

    def test_non_string_raises(self):
        with self.assertRaises(BusinessProcessingError):
            _validate_thread_title(123)

    def test_title_at_max_len_accepted(self):
        title = 'x' * 160
        result = _validate_thread_title(title)
        self.assertEqual(title, result)

    def test_title_over_max_len_raises(self):
        title = 'x' * 161
        with self.assertRaises(BusinessProcessingError):
            _validate_thread_title(title)


# ---------------------------------------------------------------------------
# _virtual_activity_row
# ---------------------------------------------------------------------------

class TestVirtualActivityRow(TestCase):

    def _make_ua_row(self, **kwargs):
        defaults = dict(
            id=10,
            user_id=2,
            activity_desc='opened case #5',
            case_id=5,
            activity_date='2026-01-01T10:00:00',
            user_login='alice',
            user_name='Alice Smith',
        )
        defaults.update(kwargs)
        row = MagicMock()
        for k, v in defaults.items():
            setattr(row, k, v)
        return row

    def test_message_id_is_negative_ua_id(self):
        ua_row = self._make_ua_row(id=10)
        result = _virtual_activity_row(ua_row, war_room_id=1)
        self.assertEqual(-10, result.message_id)

    def test_war_room_id_set(self):
        ua_row = self._make_ua_row()
        result = _virtual_activity_row(ua_row, war_room_id=99)
        self.assertEqual(99, result.war_room_id)

    def test_kind_is_case_activity(self):
        ua_row = self._make_ua_row()
        result = _virtual_activity_row(ua_row, war_room_id=1)
        self.assertEqual('case_activity', result.kind)

    def test_ref_type_is_user_activity(self):
        ua_row = self._make_ua_row()
        result = _virtual_activity_row(ua_row, war_room_id=1)
        self.assertEqual('user_activity', result.ref_type)

    def test_ref_id_is_ua_id(self):
        ua_row = self._make_ua_row(id=42)
        result = _virtual_activity_row(ua_row, war_room_id=1)
        self.assertEqual(42, result.ref_id)

    def test_body_is_activity_desc(self):
        ua_row = self._make_ua_row(activity_desc='created event Ransomware')
        result = _virtual_activity_row(ua_row, war_room_id=1)
        self.assertEqual('created event Ransomware', result.body)

    def test_parent_message_id_is_none(self):
        ua_row = self._make_ua_row()
        result = _virtual_activity_row(ua_row, war_room_id=1)
        self.assertIsNone(result.parent_message_id)

    def test_author_login_preserved(self):
        ua_row = self._make_ua_row(user_login='bob')
        result = _virtual_activity_row(ua_row, war_room_id=1)
        self.assertEqual('bob', result.author_login)

    def test_result_is_simple_namespace(self):
        ua_row = self._make_ua_row()
        result = _virtual_activity_row(ua_row, war_room_id=1)
        self.assertIsInstance(result, SimpleNamespace)
