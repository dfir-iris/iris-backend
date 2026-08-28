#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for pure helpers in war_room_tasks.py, war_room_timelines.py,
and war_room_notes.py.

No DB, no Flask context needed.
"""

from unittest import TestCase

from app.business.war_room_notes import _validate_title as note_validate_title
from app.business.war_room_tasks import _normalize_tags, _validate_title as task_validate_title
from app.business.war_room_timelines import (
    _validate_category,
    _validate_color,
    _validate_name as timeline_validate_name,
)
from app.models.errors import BusinessProcessingError


class TestTaskValidateTitle(TestCase):

    def test_valid_title_returned_stripped(self):
        self.assertEqual('My Task', task_validate_title('  My Task  '))

    def test_non_string_raises(self):
        with self.assertRaises(BusinessProcessingError):
            task_validate_title(42)

    def test_empty_string_raises(self):
        with self.assertRaises(BusinessProcessingError):
            task_validate_title('')

    def test_whitespace_only_raises(self):
        with self.assertRaises(BusinessProcessingError):
            task_validate_title('   ')

    def test_title_at_limit_accepted(self):
        result = task_validate_title('a' * 1024)
        self.assertEqual(1024, len(result))

    def test_title_over_limit_raises(self):
        with self.assertRaises(BusinessProcessingError):
            task_validate_title('a' * 1025)


class TestNormalizeTags(TestCase):

    def test_none_returns_none(self):
        self.assertIsNone(_normalize_tags(None))

    def test_empty_list_returns_none(self):
        self.assertIsNone(_normalize_tags([]))

    def test_empty_string_returns_none(self):
        self.assertIsNone(_normalize_tags(''))

    def test_single_tag_from_string(self):
        self.assertEqual('alpha', _normalize_tags('alpha'))

    def test_comma_separated_string_parsed(self):
        result = _normalize_tags('alpha,beta,gamma')
        self.assertIn('alpha', result)
        self.assertIn('beta', result)
        self.assertIn('gamma', result)

    def test_list_input_joined(self):
        result = _normalize_tags(['foo', 'bar'])
        self.assertIn('foo', result)
        self.assertIn('bar', result)

    def test_whitespace_tags_stripped(self):
        result = _normalize_tags(['  foo  ', '  bar  '])
        tags = result.split(',')
        self.assertIn('foo', tags)
        self.assertIn('bar', tags)

    def test_duplicates_removed_case_insensitive(self):
        result = _normalize_tags(['Alpha', 'alpha', 'ALPHA'])
        self.assertEqual(1, len(result.split(',')))

    def test_order_preserved_first_occurrence(self):
        result = _normalize_tags('alpha,beta,alpha')
        tags = result.split(',')
        self.assertEqual('alpha', tags[0])
        self.assertEqual('beta', tags[1])
        self.assertEqual(2, len(tags))

    def test_non_string_non_list_raises(self):
        with self.assertRaises(BusinessProcessingError):
            _normalize_tags(42)

    def test_empty_tag_entries_skipped(self):
        result = _normalize_tags('a,,b,  ,c')
        tags = result.split(',')
        self.assertNotIn('', tags)
        self.assertEqual(3, len(tags))


class TestNoteValidateTitle(TestCase):

    def test_valid_title_returned_stripped(self):
        self.assertEqual('My Note', note_validate_title('  My Note  '))

    def test_non_string_raises(self):
        with self.assertRaises(BusinessProcessingError):
            note_validate_title(99)

    def test_empty_string_raises(self):
        with self.assertRaises(BusinessProcessingError):
            note_validate_title('')

    def test_whitespace_only_raises(self):
        with self.assertRaises(BusinessProcessingError):
            note_validate_title('   ')

    def test_title_truncated_at_512(self):
        long_title = 'a' * 600
        result = note_validate_title(long_title)
        self.assertEqual(512, len(result))


class TestTimelineValidateName(TestCase):

    def test_valid_name_returned_stripped(self):
        self.assertEqual('Main Timeline', timeline_validate_name('  Main Timeline  '))

    def test_non_string_raises(self):
        with self.assertRaises(BusinessProcessingError):
            timeline_validate_name(0)

    def test_empty_raises(self):
        with self.assertRaises(BusinessProcessingError):
            timeline_validate_name('')

    def test_name_at_limit_accepted(self):
        result = timeline_validate_name('a' * 128)
        self.assertEqual(128, len(result))

    def test_name_over_limit_raises(self):
        with self.assertRaises(BusinessProcessingError):
            timeline_validate_name('a' * 129)


class TestTimelineValidateColor(TestCase):

    def test_none_returns_none(self):
        self.assertIsNone(_validate_color(None))

    def test_empty_string_returns_none(self):
        self.assertIsNone(_validate_color(''))

    def test_valid_hex_returned(self):
        self.assertEqual('#aabbcc', _validate_color('#aabbcc'))

    def test_invalid_hex_raises(self):
        with self.assertRaises(BusinessProcessingError):
            _validate_color('blue')


class TestTimelineValidateCategory(TestCase):

    def test_none_returns_none(self):
        self.assertIsNone(_validate_category(None))

    def test_empty_string_returns_none(self):
        self.assertIsNone(_validate_category(''))

    def test_whitespace_only_returns_none(self):
        self.assertIsNone(_validate_category('   '))

    def test_valid_category_returned_stripped(self):
        self.assertEqual('Malware', _validate_category('  Malware  '))

    def test_non_string_raises(self):
        with self.assertRaises(BusinessProcessingError):
            _validate_category(42)

    def test_category_over_limit_raises(self):
        with self.assertRaises(BusinessProcessingError):
            _validate_category('a' * 65)

    def test_category_at_limit_accepted(self):
        result = _validate_category('a' * 64)
        self.assertEqual(64, len(result))
