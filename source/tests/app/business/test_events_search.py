#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for the case timeline search view's business layer.

The persistence functions are replaced by stubs: what is tested here is
the arithmetic around them — date parsing, the keyset cursor, the
histogram's bar width and dense buckets, the page/`has_more` split and
the validation of bulk selections.
"""

from datetime import datetime
from datetime import timedelta
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from app.business import events_search as module
from app.business.events_search import events_search_check_ids
from app.business.events_search import events_search_decode_cursor
from app.business.events_search import events_search_encode_cursor
from app.business.events_search import events_search_parse_date
from app.business.events_search import events_search_pick_interval
from app.models.errors import BusinessProcessingError


class TestParseDate(TestCase):

    def test_empty_is_none(self):
        self.assertIsNone(events_search_parse_date('', 'start'))
        self.assertIsNone(events_search_parse_date(None, 'start'))

    def test_zulu_becomes_naive_utc(self):
        self.assertEqual(datetime(2026, 1, 2, 3, 4, 5),
                         events_search_parse_date('2026-01-02T03:04:05Z', 'start'))

    def test_offset_is_converted_to_utc(self):
        self.assertEqual(datetime(2026, 1, 2, 1, 0),
                         events_search_parse_date('2026-01-02T03:00:00+02:00', 'end'))

    def test_naive_is_taken_as_utc(self):
        self.assertEqual(datetime(2026, 1, 2), events_search_parse_date('2026-01-02', 'end'))

    def test_garbage_is_refused_with_the_parameter_name(self):
        with self.assertRaises(BusinessProcessingError) as caught:
            events_search_parse_date('yesterday', 'start')
        self.assertIn('start', caught.exception.get_message())


class TestCursor(TestCase):

    def test_round_trip(self):
        date = datetime(2026, 3, 4, 5, 6, 7, 891011)
        self.assertEqual((date, 42), events_search_decode_cursor(events_search_encode_cursor(date, 42)))

    def test_empty_is_none(self):
        self.assertIsNone(events_search_decode_cursor(''))

    def test_tampered_cursor_is_refused(self):
        with self.assertRaises(BusinessProcessingError):
            events_search_decode_cursor('not-a-cursor')


class TestPickInterval(TestCase):

    def test_one_minute_span_uses_one_second_bars(self):
        self.assertEqual(1, events_search_pick_interval(timedelta(minutes=1)))

    def test_one_day_span_uses_half_hour_bars(self):
        interval = events_search_pick_interval(timedelta(days=1))
        self.assertEqual(30 * 60, interval)
        self.assertLessEqual(86400 / interval, 120)

    def test_one_year_span(self):
        interval = events_search_pick_interval(timedelta(days=365))
        self.assertLessEqual(365 * 86400 / interval, 120)

    def test_zero_span_does_not_divide_by_zero(self):
        self.assertEqual(1, events_search_pick_interval(timedelta(0)))

    def test_huge_span_caps_at_the_widest_bar(self):
        self.assertEqual(365 * 86400, events_search_pick_interval(timedelta(days=365 * 500)))


class TestHistogram(TestCase):

    def test_buckets_are_dense_and_counts_land_in_place(self):
        start = datetime(2026, 1, 1, 0, 0)
        end = datetime(2026, 1, 1, 2, 0)
        counts = [(datetime(2026, 1, 1, 0, 0), 3), (datetime(2026, 1, 1, 1, 30), 2)]

        with patch.object(module, 'compile_event_query', return_value=None), \
                patch.object(module, 'events_search_condition', return_value='cond'), \
                patch.object(module, 'events_search_histogram', return_value=counts):
            result = module.events_search_histogram_buckets(1, start=start, end=end)

        self.assertEqual(60, result['interval_seconds'])
        self.assertEqual(120, len(result['buckets']))
        self.assertEqual(3, result['buckets'][0]['count'])
        self.assertEqual(2, result['buckets'][90]['count'])
        self.assertEqual(5, result['total'])

    def test_without_range_frames_the_matching_events(self):
        bounds = (datetime(2026, 1, 1, 10, 0), datetime(2026, 1, 1, 10, 0, 50))
        with patch.object(module, 'compile_event_query', return_value=None), \
                patch.object(module, 'events_search_condition', return_value='cond'), \
                patch.object(module, 'events_search_bounds', return_value=bounds), \
                patch.object(module, 'events_search_histogram', return_value=[]):
            result = module.events_search_histogram_buckets(1)

        self.assertEqual(1, result['interval_seconds'])
        self.assertEqual('2026-01-01T10:00:00.000000', result['buckets'][0]['start'])
        self.assertEqual(51, len(result['buckets']))

    def test_no_event_gives_no_bucket(self):
        with patch.object(module, 'compile_event_query', return_value=None), \
                patch.object(module, 'events_search_condition', return_value='cond'), \
                patch.object(module, 'events_search_bounds', return_value=(None, None)):
            result = module.events_search_histogram_buckets(1)

        self.assertEqual([], result['buckets'])
        self.assertEqual(0, result['total'])

    def test_inverted_range_is_refused(self):
        with self.assertRaises(BusinessProcessingError):
            module.events_search_histogram_buckets(1, start=datetime(2026, 2, 1), end=datetime(2026, 1, 1))


def _row(event_id, date):
    return SimpleNamespace(event_id=event_id, event_date=date)


class TestSearchPaging(TestCase):

    def _search(self, rows, cursor=None, limit=2, full_text=False):
        with patch.object(module, 'compile_event_query', return_value=None), \
                patch.object(module, 'events_search_condition', return_value='cond'), \
                patch.object(module, 'events_search_page', return_value=rows) as page, \
                patch.object(module, 'events_search_count', return_value=99) as count, \
                patch.object(module, '_hydrate', side_effect=lambda page_rows: list(page_rows)):
            result = module.events_search(1, cursor=cursor, limit=limit, full_text=full_text)
        return result, page, count

    def test_extra_row_means_more_and_is_not_returned(self):
        rows = [_row(1, datetime(2026, 1, 1)), _row(2, datetime(2026, 1, 2)), _row(3, datetime(2026, 1, 3))]
        result, _, _ = self._search(rows)
        self.assertTrue(result['has_more'])
        self.assertEqual(2, len(result['events']))
        self.assertEqual((datetime(2026, 1, 2), 2), events_search_decode_cursor(result['next_cursor']))

    def test_last_page_has_no_cursor(self):
        result, _, _ = self._search([_row(1, datetime(2026, 1, 1))])
        self.assertFalse(result['has_more'])
        self.assertIsNone(result['next_cursor'])

    def test_total_is_counted_on_the_first_page_only(self):
        result, _, count = self._search([])
        self.assertEqual(99, result['total'])

        cursor = events_search_encode_cursor(datetime(2026, 1, 1), 1)
        result, page, count = self._search([], cursor=cursor)
        self.assertIsNone(result['total'])
        count.assert_not_called()
        self.assertEqual((datetime(2026, 1, 1), 1), page.call_args.kwargs['cursor'])

    def test_limit_is_clamped(self):
        _, page, _ = self._search([], limit=10_000)
        self.assertEqual(module.EVENTS_SEARCH_MAX_LIMIT, page.call_args.kwargs['limit'])

    def test_text_is_cut_unless_full_text_is_asked(self):
        _, page, _ = self._search([])
        self.assertFalse(page.call_args.kwargs['full_text'])

        _, page, _ = self._search([], full_text=True)
        self.assertTrue(page.call_args.kwargs['full_text'])


class TestCheckIds(TestCase):

    def _check(self, event_ids, existing):
        with patch.object(module, 'events_search_existing_ids', return_value=set(existing)):
            return events_search_check_ids(1, event_ids)

    def test_deduplicates_in_order(self):
        self.assertEqual([3, 1], self._check([3, 1, 3], [1, 3]))

    def test_foreign_event_is_refused(self):
        with self.assertRaises(BusinessProcessingError):
            self._check([1, 2], [1])

    def test_empty_or_non_integer_is_refused(self):
        for value in ([], None, ['1'], [True], 'abc'):
            with self.assertRaises(BusinessProcessingError):
                self._check(value, [])

    def test_oversized_selection_is_refused(self):
        ids = list(range(module.EVENTS_BULK_MAX + 1))
        with self.assertRaises(BusinessProcessingError):
            self._check(ids, ids)
