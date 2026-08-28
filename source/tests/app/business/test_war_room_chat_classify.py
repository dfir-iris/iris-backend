#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for classify_activity_text and parse_slash in business/war_room_chat.py."""

from unittest import TestCase

from app.business.war_room_chat import classify_activity_text, parse_slash


# ---------------------------------------------------------------------------
# classify_activity_text
# ---------------------------------------------------------------------------

class TestClassifyActivityText(TestCase):

    def test_non_string_returns_other(self):
        self.assertEqual('case.other', classify_activity_text(42))

    def test_none_returns_other(self):
        self.assertEqual('case.other', classify_activity_text(None))

    def test_empty_string_returns_other(self):
        self.assertEqual('case.other', classify_activity_text(''))

    def test_no_match_returns_other(self):
        self.assertEqual('case.other', classify_activity_text('something unrecognised'))

    def test_new_case_returns_case_created(self):
        self.assertEqual('case.created', classify_activity_text('new case #42 opened'))

    def test_case_closed_returns_case_closed(self):
        self.assertEqual('case.closed', classify_activity_text('case closed'))

    def test_case_closed_case_insensitive(self):
        self.assertEqual('case.closed', classify_activity_text('Case Closed'))

    def test_case_reopened(self):
        self.assertEqual('case.reopened', classify_activity_text('case re-opened'))

    def test_case_updated(self):
        self.assertEqual('case.updated', classify_activity_text('case updated severity'))

    def test_created_note(self):
        self.assertEqual('note.created', classify_activity_text('created note "Ransom demand"'))

    def test_updated_note(self):
        self.assertEqual('note.updated', classify_activity_text('updated note #3'))

    def test_deleted_note(self):
        self.assertEqual('note.deleted', classify_activity_text('deleted note #3'))

    def test_deleted_note_revision(self):
        self.assertEqual('note.updated', classify_activity_text('deleted note revision #2 of note #3'))

    def test_added_ioc(self):
        self.assertEqual('ioc.created', classify_activity_text('added ioc evil.example.com'))

    def test_updated_ioc(self):
        self.assertEqual('ioc.updated', classify_activity_text('updated ioc #5'))

    def test_deleted_ioc(self):
        self.assertEqual('ioc.deleted', classify_activity_text('deleted ioc #5'))

    def test_added_asset(self):
        self.assertEqual('asset.created', classify_activity_text('added asset workstation-01'))

    def test_updated_asset(self):
        self.assertEqual('asset.updated', classify_activity_text('updated asset workstation-01'))

    def test_deleted_asset(self):
        self.assertEqual('asset.deleted', classify_activity_text('deleted asset workstation-01'))

    def test_removed_asset(self):
        self.assertEqual('asset.deleted', classify_activity_text('removed asset workstation-01'))

    def test_added_evidence(self):
        self.assertEqual('evidence.created', classify_activity_text('added evidence forensic.img'))

    def test_added_task(self):
        self.assertEqual('task.created', classify_activity_text('added task "Contain host"'))

    def test_added_event(self):
        self.assertEqual('event.created', classify_activity_text('added event "Lateral movement"'))

    def test_linked_alert(self):
        self.assertEqual('alert.linked', classify_activity_text('linked alert #99'))

    def test_unlinked_alert(self):
        self.assertEqual('alert.linked', classify_activity_text('unlinked alert #99'))

    def test_added_directory(self):
        self.assertEqual('directory.created', classify_activity_text('added directory Notes'))

    def test_case_reviewer_changed(self):
        self.assertEqual('case.reviewer_changed', classify_activity_text('case reviewer changed to alice'))


# ---------------------------------------------------------------------------
# parse_slash
# ---------------------------------------------------------------------------

class TestParseSlash(TestCase):

    def test_non_string_returns_none(self):
        self.assertIsNone(parse_slash(42))

    def test_none_returns_none(self):
        self.assertIsNone(parse_slash(None))

    def test_plain_message_returns_none(self):
        self.assertIsNone(parse_slash('hello world'))

    def test_leading_slash_cmd_no_args(self):
        result = parse_slash('/poll')
        self.assertEqual(('poll', ''), result)

    def test_leading_slash_cmd_with_args(self):
        result = parse_slash('/vote yes')
        self.assertEqual(('vote', 'yes'), result)

    def test_rest_stripped(self):
        result = parse_slash('/cmd   lots of spaces   ')
        self.assertEqual('cmd', result[0])
        self.assertEqual('lots of spaces', result[1])

    def test_slash_with_multiline_rest(self):
        result = parse_slash('/note line1\nline2')
        self.assertEqual('note', result[0])
        self.assertIn('line1', result[1])

    def test_uppercase_cmd_not_matched(self):
        # regex only matches [a-z]+
        self.assertIsNone(parse_slash('/Poll'))

    def test_digit_only_cmd_not_matched(self):
        self.assertIsNone(parse_slash('/123'))
