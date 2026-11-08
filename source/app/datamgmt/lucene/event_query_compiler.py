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

"""Compiles a timeline search expression into one SQLAlchemy clause.

Same contract as `compile_alert_query`: the result is a single conjunct
that the caller ANDs with `CasesEvent.case_id == …`. The case scope stays
outside the compiled clause, so no `OR` an analyst writes can reach the
events of another case.
"""

from sqlalchemy import exists
from sqlalchemy import or_

from app.datamgmt.lucene.event_fields import DEFAULT_EVENT_SEARCH_COLUMNS
from app.datamgmt.lucene.event_fields import EVENT_IS_MACROS
from app.datamgmt.lucene.event_fields import event_field_for_alias
from app.datamgmt.lucene.event_fields import event_field_raw_column
from app.datamgmt.lucene.event_fields import suggest_event_field
from app.datamgmt.lucene.query_compiler import LuceneQueryCompiler
from app.datamgmt.lucene.query_parser import lucene_parse
from app.models.cases import CasesEvent
from app.models.comments import EventComments
from app.models.models import CaseEventsAssets
from app.models.models import CaseEventsIoc

_Child = CasesEvent.__table__.alias('child_event')


def _correlated(condition):
    """EXISTS bound to the outer `cases_events` row.

    Correlation is pinned rather than left to SQLAlchemy's inference, so
    the subquery can never quietly become an uncorrelated cross join if
    the outer statement changes shape.
    """
    return exists().where(condition).correlate(CasesEvent)


class EventQueryCompiler(LuceneQueryCompiler):
    """The case timeline search bar: `GET /cases/<id>/events/search?query=…`."""

    model = CasesEvent
    default_search_columns = DEFAULT_EVENT_SEARCH_COLUMNS
    macros = EVENT_IS_MACROS

    def _field_for_alias(self, alias):
        return event_field_for_alias(alias)

    def _raw_column(self, alias):
        return event_field_raw_column(alias)

    def _suggest_field(self, alias):
        return suggest_event_field(alias)

    def _relation_exists(self, descriptor, condition=None):
        """EXISTS over the field's link table, joined to its target if any."""
        link = descriptor.link
        query = exists().where(
            getattr(link.model, link.event_column) == CasesEvent.event_id
        )
        if link.target is not None:
            query = query.where(
                getattr(link.model, link.target_fk) == getattr(link.target, link.target_pk)
            )
        if condition is not None:
            query = query.where(condition)
        return query.correlate(CasesEvent)

    def _compile_macro_named(self, name):
        if name == 'flagged':
            return CasesEvent.event_is_flagged.is_(True)

        if name == 'unflagged':
            return CasesEvent.event_is_flagged.is_not(True)

        if name == 'root':
            return CasesEvent.parent_event_id.is_(None)

        if name == 'child':
            return CasesEvent.parent_event_id.is_not(None)

        if name == 'parent':
            return _correlated(_Child.c.parent_event_id == CasesEvent.event_id)

        if name == 'summary':
            return CasesEvent.event_in_summary.is_(True)

        if name == 'graph':
            return CasesEvent.event_in_graph.is_(True)

        if name == 'commented':
            return _correlated(EventComments.comment_event_id == CasesEvent.event_id)

        return or_(
            _correlated(CaseEventsAssets.event_id == CasesEvent.event_id),
            _correlated(CaseEventsIoc.event_id == CasesEvent.event_id),
        )


def compile_event_query(query: str, user_identifier=None):
    """Parse and compile `query`; `None` when the expression is empty.

    Raises `SearchQueryError`, carrying a character offset, for anything
    the analyst needs to fix.
    """
    node = lucene_parse(query)
    if node is None:
        return None

    return EventQueryCompiler(user_identifier).compile(node)
