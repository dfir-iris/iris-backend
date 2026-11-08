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

"""Persistence for the case timeline search view.

Everything here works on one scope condition built by
`events_search_condition`: the case, the compiled search expression, an
optional date range and an optional timeline set. Rows are paged by
keyset on `(event_date, event_id)` — never OFFSET, never all-in-memory —
and only the rows of the current page are hydrated with their links.
Histogram and field summaries are aggregated by Postgres, so a case of
tens of thousands of events costs a handful of grouped queries.
"""

from sqlalchemy import and_
from sqlalchemy import distinct
from sqlalchemy import exists
from sqlalchemy import func
from sqlalchemy import literal
from sqlalchemy import select
from sqlalchemy import tuple_
from sqlalchemy.dialects.postgresql import insert

from app.db import db
from app.models.assets import AssetsType
from app.models.assets import CaseAssets
from app.models.authorization import User
from app.models.cases import CaseEventTimeline
from app.models.cases import CasesEvent
from app.models.cases import CaseTimeline
from app.models.comments import EventComments
from app.models.iocs import Ioc
from app.models.models import CaseEventCategory
from app.models.models import CaseEventsAssets
from app.models.models import CaseEventsIoc
from app.models.models import EventCategory

#: Longer descriptions and raw payloads are cut on the page and fetched in
#: full by `GET /events/<id>` when a row is expanded.
_TEXT_PREVIEW_LENGTH = 4096

#: Fields the sidebar summarises. Each maps to a builder below.
SUMMARY_FIELDS = ('category', 'source', 'tag', 'asset', 'ioc', 'timeline', 'author')


def events_search_condition(case_identifier, compiled=None, start=None, end=None, timeline_ids=None):
    """The scope every search-view query shares.

    `end` is exclusive, so adjacent histogram buckets never count an event
    twice. `timeline_ids` keeps events on *any* of the given timelines.
    """
    condition = CasesEvent.case_id == case_identifier

    if compiled is not None:
        condition = and_(condition, compiled)

    if start is not None:
        condition = and_(condition, CasesEvent.event_date >= start)

    if end is not None:
        condition = and_(condition, CasesEvent.event_date < end)

    if timeline_ids:
        condition = and_(condition, exists().where(
            CaseEventTimeline.event_id == CasesEvent.event_id,
            CaseEventTimeline.timeline_id.in_(timeline_ids),
        ).correlate(CasesEvent))

    return condition


def _text_column(column, full_text):
    if full_text:
        return column, literal(False)
    return func.left(column, _TEXT_PREVIEW_LENGTH), func.length(column) > _TEXT_PREVIEW_LENGTH


def events_search_page(condition, descending=False, cursor=None, limit=200, full_text=False):
    """One keyset page; `limit + 1` rows are read so the caller knows if more follow.

    `cursor` is the `(event_date, event_id)` of the last row already shown.
    Descriptions and raw data are cut to a preview unless `full_text`.
    """
    key = tuple_(CasesEvent.event_date, CasesEvent.event_id)
    if cursor is not None:
        boundary = tuple_(*cursor)
        condition = and_(condition, key < boundary if descending else key > boundary)

    if descending:
        ordering = (CasesEvent.event_date.desc(), CasesEvent.event_id.desc())
    else:
        ordering = (CasesEvent.event_date.asc(), CasesEvent.event_id.asc())

    content, content_truncated = _text_column(CasesEvent.event_content, full_text)
    raw, raw_truncated = _text_column(CasesEvent.event_raw, full_text)

    statement = (
        select(
            CasesEvent.event_id,
            CasesEvent.event_uuid,
            CasesEvent.parent_event_id,
            CasesEvent.event_date,
            CasesEvent.event_date_wtz,
            CasesEvent.event_tz,
            CasesEvent.event_added,
            CasesEvent.event_title,
            CasesEvent.event_source,
            CasesEvent.event_tags,
            CasesEvent.event_color,
            CasesEvent.event_in_summary,
            CasesEvent.event_in_graph,
            CasesEvent.event_is_flagged,
            content.label('event_content'),
            content_truncated.label('event_content_truncated'),
            raw.label('event_raw'),
            raw_truncated.label('event_raw_truncated'),
            User.user.label('user'),
            EventCategory.id.label('event_category_id'),
            EventCategory.name.label('category_name'),
        )
        .select_from(CasesEvent)
        .outerjoin(User, User.id == CasesEvent.user_id)
        .outerjoin(CaseEventCategory, CaseEventCategory.event_id == CasesEvent.event_id)
        .outerjoin(EventCategory, EventCategory.id == CaseEventCategory.category_id)
        .where(condition)
        .order_by(*ordering)
        .limit(limit + 1)
    )
    return db.session.execute(statement).all()


def events_search_count(condition):
    statement = select(func.count(CasesEvent.event_id)).where(condition)
    return db.session.execute(statement).scalar() or 0


def events_search_page_assets(event_ids):
    if not event_ids:
        return []

    statement = (
        select(
            CaseEventsAssets.event_id,
            CaseEventsAssets.role,
            CaseAssets.asset_id,
            CaseAssets.asset_name,
            CaseAssets.asset_ip,
            CaseAssets.asset_description,
            CaseAssets.asset_compromise_status_id,
            AssetsType.asset_name.label('type'),
        )
        .join(CaseAssets, CaseAssets.asset_id == CaseEventsAssets.asset_id)
        .outerjoin(AssetsType, AssetsType.asset_id == CaseAssets.asset_type_id)
        .where(CaseEventsAssets.event_id.in_(event_ids))
        .order_by(CaseAssets.asset_name)
    )
    return db.session.execute(statement).all()


def events_search_page_iocs(event_ids):
    if not event_ids:
        return []

    statement = (
        select(
            CaseEventsIoc.event_id,
            CaseEventsIoc.role,
            Ioc.ioc_id,
            Ioc.ioc_value,
            Ioc.ioc_description,
        )
        .join(Ioc, Ioc.ioc_id == CaseEventsIoc.ioc_id)
        .where(CaseEventsIoc.event_id.in_(event_ids))
        .order_by(Ioc.ioc_value)
    )
    return db.session.execute(statement).all()


def events_search_page_timelines(event_ids):
    if not event_ids:
        return []

    statement = (
        select(CaseEventTimeline.event_id, CaseEventTimeline.timeline_id)
        .where(CaseEventTimeline.event_id.in_(event_ids))
    )
    return db.session.execute(statement).all()


def events_search_page_comment_counts(event_ids):
    if not event_ids:
        return []

    statement = (
        select(EventComments.comment_event_id, func.count(distinct(EventComments.comment_id)))
        .where(EventComments.comment_event_id.in_(event_ids))
        .group_by(EventComments.comment_event_id)
    )
    return db.session.execute(statement).all()


def events_search_page_children_counts(event_ids):
    if not event_ids:
        return []

    statement = (
        select(CasesEvent.parent_event_id, func.count(CasesEvent.event_id))
        .where(CasesEvent.parent_event_id.in_(event_ids))
        .group_by(CasesEvent.parent_event_id)
    )
    return db.session.execute(statement).all()


def events_search_bounds(condition):
    """`(earliest, latest)` event date in scope; `(None, None)` when empty."""
    statement = select(func.min(CasesEvent.event_date), func.max(CasesEvent.event_date)).where(condition)
    row = db.session.execute(statement).one()
    return row[0], row[1]


def events_search_histogram(condition, interval, origin):
    """`[(bucket_start, count)]` for every non-empty bucket, in date order.

    `interval` is a `timedelta`; `date_bin` cannot bin by month, so the
    caller keeps to fixed-width intervals.
    """
    bucket = func.date_bin(interval, CasesEvent.event_date, origin).label('bucket')
    statement = (
        select(bucket, func.count(CasesEvent.event_id))
        .where(condition, CasesEvent.event_date.is_not(None))
        .group_by(bucket)
        .order_by(bucket)
    )
    return db.session.execute(statement).all()


def _top_values(value, condition, joins=(), limit=10, extra=None):
    """`(value, events, distinct values)` rows, most frequent first."""
    statement = select(
        value.label('value'),
        func.count(distinct(CasesEvent.event_id)).label('events'),
        func.count().over().label('distinct_values'),
    ).select_from(CasesEvent)

    for target, on in joins:
        statement = statement.join(target, on)

    statement = statement.where(condition, value.is_not(None), value != '')
    if extra is not None:
        statement = statement.where(extra)

    statement = (
        statement.group_by(value)
        .order_by(func.count(distinct(CasesEvent.event_id)).desc(), value)
        .limit(limit)
    )
    return db.session.execute(statement).all()


def _top_tags(condition, limit):
    """Tags are a comma-separated column: unnest before grouping."""
    raw = (
        select(
            CasesEvent.event_id.label('event_id'),
            func.unnest(func.string_to_array(CasesEvent.event_tags, ',')).label('tag'),
        )
        .where(condition, CasesEvent.event_tags.is_not(None))
        .subquery()
    )
    tag = func.trim(raw.c.tag)
    statement = (
        select(
            tag.label('value'),
            func.count(distinct(raw.c.event_id)).label('events'),
            func.count().over().label('distinct_values'),
        )
        .where(tag != '')
        .group_by(tag)
        .order_by(func.count(distinct(raw.c.event_id)).desc(), tag)
        .limit(limit)
    )
    return db.session.execute(statement).all()


def events_search_field_summary(condition, field, limit=10):
    """Top values of one `SUMMARY_FIELDS` entry among the events in scope."""
    if field == 'tag':
        return _top_tags(condition, limit)

    if field == 'source':
        return _top_values(CasesEvent.event_source, condition, limit=limit)

    if field == 'author':
        return _top_values(User.user, condition, joins=((User, User.id == CasesEvent.user_id),), limit=limit)

    if field == 'category':
        return _top_values(EventCategory.name, condition, joins=(
            (CaseEventCategory, CaseEventCategory.event_id == CasesEvent.event_id),
            (EventCategory, EventCategory.id == CaseEventCategory.category_id),
        ), limit=limit)

    if field == 'asset':
        return _top_values(CaseAssets.asset_name, condition, joins=(
            (CaseEventsAssets, CaseEventsAssets.event_id == CasesEvent.event_id),
            (CaseAssets, CaseAssets.asset_id == CaseEventsAssets.asset_id),
        ), limit=limit)

    if field == 'ioc':
        return _top_values(Ioc.ioc_value, condition, joins=(
            (CaseEventsIoc, CaseEventsIoc.event_id == CasesEvent.event_id),
            (Ioc, Ioc.ioc_id == CaseEventsIoc.ioc_id),
        ), limit=limit)

    if field == 'timeline':
        return _top_values(CaseTimeline.name, condition, joins=(
            (CaseEventTimeline, CaseEventTimeline.event_id == CasesEvent.event_id),
            (CaseTimeline, CaseTimeline.timeline_id == CaseEventTimeline.timeline_id),
        ), limit=limit)

    raise ValueError(f'Unknown summary field {field}')


def events_search_existing_ids(case_identifier, event_ids):
    """The subset of `event_ids` that are events of the case."""
    if not event_ids:
        return set()

    statement = select(CasesEvent.event_id).where(
        CasesEvent.case_id == case_identifier,
        CasesEvent.event_id.in_(event_ids),
    )
    return set(db.session.execute(statement).scalars().all())


def events_search_get_events(case_identifier, event_ids):
    """The ORM rows of the listed events of the case, for bulk updates."""
    if not event_ids:
        return []

    return (
        CasesEvent.query
        .filter(CasesEvent.case_id == case_identifier, CasesEvent.event_id.in_(event_ids))
        .order_by(CasesEvent.event_id)
        .all()
    )


def events_timelines_assign(event_ids, timeline_id, detach_from=None, detach_all=False):
    """Put events on `timeline_id`, optionally taking them off others first.

    `detach_all` removes every existing membership; otherwise `detach_from`
    lists the timelines to leave. Both happen in the same transaction as
    the insert, so a move never leaves an event on no timeline halfway.
    Returns the number of memberships created.
    """
    if not event_ids:
        return 0

    if detach_all:
        CaseEventTimeline.query.filter(
            CaseEventTimeline.event_id.in_(event_ids)
        ).delete(synchronize_session=False)
    elif detach_from:
        CaseEventTimeline.query.filter(
            CaseEventTimeline.event_id.in_(event_ids),
            CaseEventTimeline.timeline_id.in_(detach_from),
        ).delete(synchronize_session=False)

    statement = (
        insert(CaseEventTimeline)
        .values([{'event_id': event_id, 'timeline_id': timeline_id} for event_id in event_ids])
        .on_conflict_do_nothing(index_elements=['event_id', 'timeline_id'])
    )
    created = db.session.execute(statement).rowcount
    db.session.commit()
    return created
