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

"""The vocabulary the case timeline search bar speaks.

The timeline counterpart of `alert_fields`: one table read by the
compiler, the `search-schema` route and the OpenAPI description. The
grammar is the alert bar's, so an analyst who can filter the alert queue
can filter a timeline without learning anything new.

`CasesEvent` carries no relationship attributes for its assets, IOCs,
timelines, category or comments — they live in association tables — so
every relation here names its link table explicitly and the compiler
writes the EXISTS itself.
"""

from dataclasses import dataclass
from difflib import get_close_matches
from typing import Optional

from app.datamgmt.lucene.alert_fields import EnumLookup
from app.datamgmt.lucene.alert_fields import KIND_DATE
from app.datamgmt.lucene.alert_fields import KIND_INTEGER
from app.datamgmt.lucene.alert_fields import KIND_MACRO
from app.datamgmt.lucene.alert_fields import KIND_MEMBERSHIP
from app.datamgmt.lucene.alert_fields import KIND_RELATION
from app.datamgmt.lucene.alert_fields import KIND_TEXT
from app.datamgmt.lucene.alert_fields import KIND_USER
from app.datamgmt.lucene.alert_fields import KIND_UUID
from app.models.assets import CaseAssets
from app.models.authorization import User
from app.models.cases import CaseEventTimeline
from app.models.cases import CasesEvent
from app.models.cases import CaseTimeline
from app.models.comments import Comments
from app.models.comments import EventComments
from app.models.iocs import Ioc
from app.models.models import CaseEventCategory
from app.models.models import CaseEventsAssets
from app.models.models import CaseEventsIoc
from app.models.models import EventCategory

#: Columns a field-less term searches, OR'd together. Every one carries a
#: trigram index (see the `cases_events` search migration) — leave one
#: out and Postgres cannot turn the OR into a BitmapOr, so the whole
#: expression falls back to a sequential scan.
DEFAULT_EVENT_SEARCH_COLUMNS = (
    'event_title',
    'event_content',
    'event_source',
    'event_tags',
    'event_raw',
)

#: `is:` values.
EVENT_IS_MACROS = {
    'flagged': 'Event is flagged',
    'unflagged': 'Event is not flagged',
    'root': 'Event has no parent event',
    'child': 'Event is the child of another event',
    'parent': 'Event has at least one child event',
    'summary': 'Event is shown in the case summary',
    'graph': 'Event is shown in the case graph',
    'commented': 'Event has at least one comment',
    'linked': 'Event is linked to at least one asset or IOC',
}


@dataclass(frozen=True)
class EventLink:
    """How an event reaches a related row.

    `model.event_column` points at the event. When the searched value
    lives one table further — an asset's name, a category's name —
    `target` is that table, joined on `model.target_fk == target.target_pk`.
    """

    model: type
    event_column: str
    target: Optional[type] = None
    target_fk: Optional[str] = None
    target_pk: Optional[str] = None


@dataclass(frozen=True)
class EventField:
    """One searchable alias. Read by the compiler the way `AlertField` is."""

    alias: str
    kind: str
    description: str
    column: Optional[str] = None
    lookup: Optional[EnumLookup] = None
    link: Optional[EventLink] = None
    #: `(model, column)` pairs the relation's value is matched against.
    relation_columns: tuple = ()
    synonyms: tuple = ()
    ordered: bool = False
    #: Whether `field:none` means "no related row" rather than the text
    #: "none". Only for relations where having none is a state worth
    #: asking about.
    accepts_none: bool = False

    def is_enumerable(self) -> bool:
        return self.kind == KIND_MACRO


_ASSET_LINK = EventLink(CaseEventsAssets, 'event_id', CaseAssets, 'asset_id', 'asset_id')
_IOC_LINK = EventLink(CaseEventsIoc, 'event_id', Ioc, 'ioc_id', 'ioc_id')
_CATEGORY_LINK = EventLink(CaseEventCategory, 'event_id', EventCategory, 'category_id', 'id')
_TIMELINE_LINK = EventLink(CaseEventTimeline, 'event_id', CaseTimeline, 'timeline_id', 'timeline_id')
_TIMELINE_ID_LINK = EventLink(CaseEventTimeline, 'event_id')
_COMMENT_LINK = EventLink(EventComments, 'comment_event_id', Comments, 'comment_id', 'comment_id')

_AUTHOR_LOOKUP = EnumLookup(User, 'id', 'user', alternates=('name', 'email'))

_FIELDS = (
    EventField('title', KIND_TEXT, 'Event title', column='event_title'),
    EventField('description', KIND_TEXT, 'Event description', column='event_content',
               synonyms=('desc', 'content')),
    EventField('source', KIND_TEXT, 'Where the event was observed', column='event_source'),
    EventField('raw', KIND_TEXT, 'Raw event data', column='event_raw'),
    EventField('tag', KIND_TEXT, 'Event tags', column='event_tags', synonyms=('tags',)),
    EventField('color', KIND_TEXT, 'Event colour', column='event_color'),

    EventField('id', KIND_INTEGER, 'Event identifier', column='event_id', ordered=True),
    EventField('parent', KIND_INTEGER, 'Identifier of the parent event',
               column='parent_event_id', ordered=True),
    EventField('uuid', KIND_UUID, 'Event UUID', column='event_uuid'),

    EventField('date', KIND_DATE, 'When the event happened (UTC)', column='event_date',
               synonyms=('time',), ordered=True),
    EventField('added', KIND_DATE, 'When the event was added to the case',
               column='event_added', ordered=True),

    EventField('author', KIND_USER, "Who added the event — a login or 'me'",
               column='user_id', lookup=_AUTHOR_LOOKUP, synonyms=('user',)),

    EventField('category', KIND_RELATION, "Event category, or 'none'",
               link=_CATEGORY_LINK, relation_columns=((EventCategory, 'name'),),
               synonyms=('cat',), accepts_none=True),
    EventField('asset', KIND_RELATION, "Name of an asset linked to the event, or 'none'",
               link=_ASSET_LINK, relation_columns=((CaseAssets, 'asset_name'),),
               accepts_none=True),
    EventField('asset_ip', KIND_RELATION, 'IP of an asset linked to the event',
               link=_ASSET_LINK, relation_columns=((CaseAssets, 'asset_ip'),)),
    EventField('ioc', KIND_RELATION, "Value of an IOC linked to the event, or 'none'",
               link=_IOC_LINK, relation_columns=((Ioc, 'ioc_value'),), accepts_none=True),
    EventField('timeline', KIND_RELATION, "Name of a timeline the event is on, or 'none'",
               link=_TIMELINE_LINK, relation_columns=((CaseTimeline, 'name'),),
               accepts_none=True),
    EventField('timeline_id', KIND_MEMBERSHIP, "Identifier of a timeline the event is on, or 'none'",
               link=_TIMELINE_ID_LINK, relation_columns=((CaseEventTimeline, 'timeline_id'),)),
    EventField('comment', KIND_RELATION, 'Text of a comment on the event',
               link=_COMMENT_LINK, relation_columns=((Comments, 'comment_text'),)),

    EventField('is', KIND_MACRO, 'Event state shorthand'),
)

_BY_ALIAS = {}
for _field in _FIELDS:
    _BY_ALIAS[_field.alias] = _field
    for _synonym in _field.synonyms:
        _BY_ALIAS[_synonym] = _field


def event_field_for_alias(alias: str) -> Optional[EventField]:
    return _BY_ALIAS.get(alias)


def event_field_raw_column(alias: str):
    """The `CasesEvent` column an alias names literally, or `None`.

    Limited to the `event_*` columns: `case_id` in particular must never
    be reachable from the bar, even though the case scope is applied
    outside the compiled clause anyway.
    """
    if not alias.startswith('event_'):
        return None
    return getattr(CasesEvent, alias, None)


def suggest_event_field(alias: str) -> Optional[str]:
    matches = get_close_matches(alias.lower(), list(_BY_ALIAS), n=1, cutoff=0.75)
    if matches:
        return matches[0]
    return None


def event_field_catalogue() -> list:
    """The catalogue as plain data, for `GET /cases/<id>/events/search-schema`."""
    described = []
    for entry in _FIELDS:
        described.append({
            'alias': entry.alias,
            'synonyms': list(entry.synonyms),
            'kind': entry.kind,
            'description': entry.description,
            'enumerable': entry.is_enumerable(),
            'ordered': entry.ordered,
            'values': sorted(EVENT_IS_MACROS) if entry.kind == KIND_MACRO else [],
        })
    return described
