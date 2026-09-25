#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for the pure helper functions in datamgmt/alerts/alerts_db.py."""

import datetime
from unittest import TestCase

from app.datamgmt.alerts.alerts_db import _new_case_tag_titles
from app.datamgmt.alerts.alerts_db import _parse_case_tags
from app.datamgmt.alerts.alerts_db import _parse_inclusive_date_range


class TestParseInclusiveDateRange(TestCase):

    def test_none_start_returns_none_none(self):
        start, end = _parse_inclusive_date_range(None, '2026-01-15')
        self.assertIsNone(start)
        self.assertIsNone(end)

    def test_none_end_returns_none_none(self):
        start, end = _parse_inclusive_date_range('2026-01-01', None)
        self.assertIsNone(start)
        self.assertIsNone(end)

    def test_both_none_returns_none_none(self):
        start, end = _parse_inclusive_date_range(None, None)
        self.assertIsNone(start)
        self.assertIsNone(end)

    def test_invalid_start_returns_none_none(self):
        start, end = _parse_inclusive_date_range('not-a-date', '2026-01-15')
        self.assertIsNone(start)
        self.assertIsNone(end)

    def test_invalid_end_returns_none_none(self):
        start, end = _parse_inclusive_date_range('2026-01-01', 'garbage')
        self.assertIsNone(start)
        self.assertIsNone(end)

    def test_date_only_end_extended_to_end_of_day(self):
        _, end = _parse_inclusive_date_range('2026-01-01', '2026-01-15')
        self.assertIsNotNone(end)
        self.assertEqual(23, end.hour)
        self.assertEqual(59, end.minute)
        self.assertEqual(59, end.second)
        self.assertEqual(999999, end.microsecond)

    def test_datetime_end_not_extended(self):
        _, end = _parse_inclusive_date_range('2026-01-01T00:00:00', '2026-01-15T12:30:00')
        self.assertIsNotNone(end)
        self.assertEqual(12, end.hour)
        self.assertEqual(30, end.minute)
        self.assertEqual(0, end.second)

    def test_valid_dates_returned_as_datetimes(self):
        start, end = _parse_inclusive_date_range('2026-01-01', '2026-03-31')
        self.assertIsInstance(start, datetime.datetime)
        self.assertIsInstance(end, datetime.datetime)

    def test_start_date_not_modified(self):
        start, _ = _parse_inclusive_date_range('2026-01-01', '2026-01-15')
        self.assertEqual(2026, start.year)
        self.assertEqual(1, start.month)
        self.assertEqual(1, start.day)

    def test_end_date_value_correct(self):
        _, end = _parse_inclusive_date_range('2026-01-01', '2026-03-15')
        self.assertEqual(2026, end.year)
        self.assertEqual(3, end.month)
        self.assertEqual(15, end.day)


class TestParseCaseTags(TestCase):

    def test_none_returns_empty_list(self):
        # The escalate/merge routes read `case_tags` straight off the request
        # body, so an omitted key arrives as None.
        self.assertEqual([], _parse_case_tags(None))

    def test_empty_string_returns_empty_list(self):
        self.assertEqual([], _parse_case_tags(''))

    def test_single_tag(self):
        self.assertEqual(['phishing'], _parse_case_tags('phishing'))

    def test_multiple_tags_split_on_comma(self):
        self.assertEqual(['phishing', 'malware'], _parse_case_tags('phishing,malware'))

    def test_surrounding_whitespace_stripped(self):
        self.assertEqual(['phishing', 'malware'], _parse_case_tags(' phishing , malware '))

    def test_empty_parts_dropped(self):
        self.assertEqual(['phishing', 'malware'], _parse_case_tags('phishing,,malware,'))

    def test_whitespace_only_parts_dropped(self):
        self.assertEqual(['phishing'], _parse_case_tags('phishing,   ,'))

    def test_only_separators_returns_empty_list(self):
        self.assertEqual([], _parse_case_tags(',,,'))

    def test_duplicates_removed(self):
        # `case_tags` is unique on (case_id, tag_id): appending the same
        # get-or-create row twice would fail the constraint.
        self.assertEqual(['phishing'], _parse_case_tags('phishing,phishing'))

    def test_duplicates_removed_after_stripping(self):
        self.assertEqual(['phishing'], _parse_case_tags('phishing, phishing '))

    def test_order_preserved(self):
        self.assertEqual(['c', 'a', 'b'], _parse_case_tags('c,a,b'))


class _StubTag:
    """Stands in for a `Tags` row: only the title is compared."""

    def __init__(self, tag_title):
        self.tag_title = tag_title


class _StubCase:
    """Stands in for a `Cases` row carrying an already-attached tag list."""

    def __init__(self, tag_titles):
        self.tags = [_StubTag(title) for title in tag_titles]


class TestNewCaseTagTitles(TestCase):

    def test_a_case_with_no_tags_takes_them_all(self):
        case = _StubCase([])
        self.assertEqual(['phishing', 'malware'], _new_case_tag_titles(case, 'phishing,malware'))

    def test_a_tag_the_case_already_has_is_skipped(self):
        # The regression. `Tags.save()` is get-or-create, so re-applying an
        # existing title hands back the row the case already holds, and
        # appending it again breaks the unique (case_id, tag_id).
        case = _StubCase(['phishing'])
        self.assertEqual(['malware'], _new_case_tag_titles(case, 'phishing,malware'))

    def test_a_case_that_has_every_tag_takes_none(self):
        case = _StubCase(['phishing', 'malware'])
        self.assertEqual([], _new_case_tag_titles(case, 'phishing,malware'))

    def test_existing_tags_are_matched_after_stripping(self):
        case = _StubCase(['phishing'])
        self.assertEqual([], _new_case_tag_titles(case, ' phishing '))

    def test_duplicates_in_the_string_are_still_removed(self):
        case = _StubCase([])
        self.assertEqual(['phishing'], _new_case_tag_titles(case, 'phishing,phishing'))

    def test_none_returns_empty_list(self):
        self.assertEqual([], _new_case_tag_titles(_StubCase(['phishing']), None))

    def test_empty_string_returns_empty_list(self):
        self.assertEqual([], _new_case_tag_titles(_StubCase([]), ''))

    def test_order_is_preserved(self):
        case = _StubCase(['b'])
        self.assertEqual(['c', 'a'], _new_case_tag_titles(case, 'c,b,a'))

    def test_matching_is_case_sensitive(self):
        # `tags.tag_title` is unique as stored, so 'Phishing' and 'phishing'
        # are different rows and both may legitimately attach.
        case = _StubCase(['phishing'])
        self.assertEqual(['Phishing'], _new_case_tag_titles(case, 'Phishing'))
