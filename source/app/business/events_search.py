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

"""The case timeline search view: query, histogram and field summaries.

Every entry point takes the same scope — the case, a search expression,
an optional `[start, end)` range and an optional timeline set — so the
list, the histogram and the sidebar always describe the same events.
Dates cross the API as ISO 8601 and are compared as naive UTC, which is
how `CasesEvent.event_date` is stored.
"""

import base64
import binascii
from datetime import datetime
from datetime import timedelta
from datetime import timezone

from app.datamgmt.case.case_events_search_db import SUMMARY_FIELDS
from app.datamgmt.case.case_events_search_db import events_search_bounds
from app.datamgmt.case.case_events_search_db import events_search_condition
from app.datamgmt.case.case_events_search_db import events_search_count
from app.datamgmt.case.case_events_search_db import events_search_existing_ids
from app.datamgmt.case.case_events_search_db import events_search_field_summary
from app.datamgmt.case.case_events_search_db import events_search_histogram
from app.datamgmt.case.case_events_search_db import events_search_page
from app.datamgmt.case.case_events_search_db import events_search_page_assets
from app.datamgmt.case.case_events_search_db import events_search_page_children_counts
from app.datamgmt.case.case_events_search_db import events_search_page_comment_counts
from app.datamgmt.case.case_events_search_db import events_search_page_iocs
from app.datamgmt.case.case_events_search_db import events_search_page_timelines
from app.datamgmt.lucene.event_fields import event_field_catalogue
from app.datamgmt.lucene.event_query_compiler import compile_event_query
from app.models.assets import CompromiseStatus
from app.models.errors import BusinessProcessingError

EVENTS_SEARCH_DEFAULT_LIMIT = 200
EVENTS_SEARCH_MAX_LIMIT = 1000

#: Largest selection a bulk action accepts in one request.
EVENTS_BULK_MAX = 5000

#: Histogram widths, narrowest first. The narrowest one that keeps the
#: span under `_HISTOGRAM_TARGET_BUCKETS` bars wins. Fixed widths only:
#: `date_bin` has no notion of a calendar month.
_HISTOGRAM_INTERVALS = (
    1, 5, 10, 30,
    60, 5 * 60, 10 * 60, 30 * 60,
    3600, 3 * 3600, 6 * 3600, 12 * 3600,
    86400, 2 * 86400, 7 * 86400, 14 * 86400, 30 * 86400, 90 * 86400, 365 * 86400,
)
_HISTOGRAM_TARGET_BUCKETS = 120

#: Bins are aligned on this instant, so the same range always yields the
#: same bars whatever the first event is.
_HISTOGRAM_ORIGIN = datetime(2000, 1, 1)

_SUMMARY_TOP_VALUES = 10


def events_search_parse_date(value, name):
    """ISO 8601 to naive UTC; `None` for an empty value."""
    if value is None or value == '':
        return None

    text = value.strip()
    if text.endswith('Z'):
        text = f'{text[:-1]}+00:00'

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise BusinessProcessingError(f'{name} must be an ISO 8601 date')

    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _format_date(value):
    if value is None:
        return None
    return value.strftime('%Y-%m-%dT%H:%M:%S.%f')


def events_search_encode_cursor(event_date, event_id):
    raw = f'{_format_date(event_date)}|{event_id}'
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip('=')


def events_search_decode_cursor(cursor):
    if not cursor:
        return None

    try:
        padded = cursor + '=' * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(padded.encode()).decode()
        date_part, id_part = raw.split('|', 1)
        return datetime.fromisoformat(date_part), int(id_part)
    except (ValueError, binascii.Error, UnicodeDecodeError):
        raise BusinessProcessingError('Invalid cursor')


def events_search_pick_interval(span):
    """The bar width, in seconds, for a `timedelta` span."""
    seconds = max(span.total_seconds(), 1)
    for interval in _HISTOGRAM_INTERVALS:
        if seconds / interval <= _HISTOGRAM_TARGET_BUCKETS:
            return interval
    return _HISTOGRAM_INTERVALS[-1]


def _bin(value, interval):
    """The start of the `date_bin` bucket `value` falls in."""
    step = timedelta(seconds=interval)
    return _HISTOGRAM_ORIGIN + ((value - _HISTOGRAM_ORIGIN) // step) * step


def _scope(case_identifier, query, start, end, timeline_ids, user_identifier):
    if start is not None and end is not None and start >= end:
        raise BusinessProcessingError('start must be before end')

    compiled = compile_event_query(query or '', user_identifier=user_identifier)
    return events_search_condition(case_identifier, compiled=compiled, start=start, end=end,
                                   timeline_ids=timeline_ids)


def _serialize_asset(row):
    return {
        'id': row.asset_id,
        'name': f'{row.asset_name} ({row.type})',
        'asset_name': row.asset_name,
        'asset_type': row.type,
        'ip': row.asset_ip,
        'description': row.asset_description,
        'compromised': row.asset_compromise_status_id == CompromiseStatus.compromised.value,
        'role': row.role or 'involved',
    }


def _serialize_ioc(row):
    return {
        'id': row.ioc_id,
        'name': row.ioc_value,
        'ioc_value': row.ioc_value,
        'description': row.ioc_description,
        'role': row.role or 'involved',
    }


def _hydrate(rows):
    event_ids = [row.event_id for row in rows]

    assets = {}
    for row in events_search_page_assets(event_ids):
        assets.setdefault(row.event_id, []).append(_serialize_asset(row))

    iocs = {}
    for row in events_search_page_iocs(event_ids):
        iocs.setdefault(row.event_id, []).append(_serialize_ioc(row))

    timelines = {}
    for event_id, timeline_id in events_search_page_timelines(event_ids):
        timelines.setdefault(event_id, []).append(timeline_id)

    comments = dict(events_search_page_comment_counts(event_ids))
    children = dict(events_search_page_children_counts(event_ids))

    events = []
    for row in rows:
        event = row._asdict()
        event['event_uuid'] = str(row.event_uuid) if row.event_uuid else None
        event['event_date'] = _format_date(row.event_date)
        event['event_date_wtz'] = _format_date(row.event_date_wtz)
        event['event_added'] = _format_date(row.event_added)
        event['event_content_truncated'] = bool(row.event_content_truncated)
        event['event_raw_truncated'] = bool(row.event_raw_truncated)
        event['assets'] = assets.get(row.event_id, [])
        event['iocs'] = iocs.get(row.event_id, [])
        event['timeline_ids'] = sorted(timelines.get(row.event_id, []))
        event['comments_count'] = comments.get(row.event_id, 0)
        event['children_count'] = children.get(row.event_id, 0)
        events.append(event)
    return events


def events_search(case_identifier, query=None, start=None, end=None, timeline_ids=None,
                  descending=False, cursor=None, limit=EVENTS_SEARCH_DEFAULT_LIMIT,
                  user_identifier=None, full_text=False):
    """One page of matching events, oldest first unless `descending`.

    `total` is only counted for the first page (no `cursor`): later pages
    belong to the same result set, and counting it again on every scroll
    would double the cost of each one.
    """
    limit = max(1, min(int(limit), EVENTS_SEARCH_MAX_LIMIT))
    condition = _scope(case_identifier, query, start, end, timeline_ids, user_identifier)

    rows = events_search_page(condition, descending=descending,
                              cursor=events_search_decode_cursor(cursor), limit=limit,
                              full_text=full_text)
    has_more = len(rows) > limit
    rows = rows[:limit]

    next_cursor = None
    if has_more and rows:
        last = rows[-1]
        next_cursor = events_search_encode_cursor(last.event_date, last.event_id)

    return {
        'events': _hydrate(rows),
        'next_cursor': next_cursor,
        'has_more': has_more,
        'total': None if cursor else events_search_count(condition),
    }


def events_search_histogram_buckets(case_identifier, query=None, start=None, end=None,
                                    timeline_ids=None, user_identifier=None):
    """Event counts per bar over `[start, end)`, empty bars included.

    Without a range, the span of the matching events is used, so the
    histogram always frames what the search found.
    """
    condition = _scope(case_identifier, query, start, end, timeline_ids, user_identifier)

    if start is None or end is None:
        earliest, latest = events_search_bounds(condition)
        if earliest is None:
            return {'interval_seconds': None, 'start': _format_date(start), 'end': _format_date(end),
                    'buckets': [], 'total': 0}
        start = start if start is not None else earliest
        end = end if end is not None else latest + timedelta(microseconds=1)

    interval = events_search_pick_interval(end - start)
    step = timedelta(seconds=interval)
    counts = dict(events_search_histogram(condition, step, _HISTOGRAM_ORIGIN))

    buckets = []
    total = 0
    bucket = _bin(start, interval)
    while bucket < end:
        count = counts.get(bucket, 0)
        total += count
        buckets.append({'start': _format_date(bucket), 'count': count})
        bucket += step

    return {
        'interval_seconds': interval,
        'start': _format_date(start),
        'end': _format_date(end),
        'buckets': buckets,
        'total': total,
    }


def events_search_fields(case_identifier, query=None, start=None, end=None, timeline_ids=None,
                         user_identifier=None):
    """Top values of each summarised field among the matching events."""
    condition = _scope(case_identifier, query, start, end, timeline_ids, user_identifier)

    fields = {}
    for field in SUMMARY_FIELDS:
        rows = events_search_field_summary(condition, field, limit=_SUMMARY_TOP_VALUES)
        fields[field] = {
            'values': [{'value': row.value, 'count': row.events} for row in rows],
            'distinct': rows[0].distinct_values if rows else 0,
        }
    return {'fields': fields}


def events_search_check_ids(case_identifier, event_ids):
    """`event_ids` deduplicated, or raise unless every one is an event of the case.

    Guards the bulk actions of the view, which act on a selection the
    client sends back as a list of identifiers.
    """
    if not isinstance(event_ids, (list, tuple)) or not event_ids:
        raise BusinessProcessingError('event_ids must be a non-empty list of integers')

    cleaned = []
    seen = set()
    for event_id in event_ids:
        if not isinstance(event_id, int) or isinstance(event_id, bool):
            raise BusinessProcessingError('event_ids must be a non-empty list of integers')
        if event_id not in seen:
            seen.add(event_id)
            cleaned.append(event_id)

    if len(cleaned) > EVENTS_BULK_MAX:
        raise BusinessProcessingError(f'At most {EVENTS_BULK_MAX} events can be changed at once')

    if events_search_existing_ids(case_identifier, cleaned) != seen:
        raise BusinessProcessingError('One or more events do not belong to this case')

    return cleaned


def events_search_vocabulary():
    """The fields the timeline search bar understands, as plain data."""
    return event_field_catalogue()
