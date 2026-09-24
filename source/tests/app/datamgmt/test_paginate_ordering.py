#  IRIS Source Code
#  Copyright (C) 2025 - DFIR-IRIS
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

"""Regression tests for datamgmt.filtering.paginate.

`paginate()` used to apply an ORDER BY only when the caller supplied
`order_by`. `parse_pagination_parameters` defaults it to None and only 3 of 26
routes passed a default, so the common path paginated an unordered query and
silently dropped rows.

The important case here is the one where `order_by` is None. A test that
supplied one would have passed against the broken code.
"""

from unittest import TestCase

from sqlalchemy import Column
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy.orm import declarative_base

from app.datamgmt.filtering import paginate
from app.models.pagination_parameters import PaginationParameters

_Base = declarative_base()


class _Widget(_Base):
    __tablename__ = 'test_paginate_widget'

    widget_id = Column(Integer, primary_key=True)
    name = Column(String)


class _RecordingQuery:
    """Records `order_by` clauses instead of talking to a database."""

    def __init__(self):
        self.order_by_clauses = []
        self.paginate_kwargs = None

    def order_by(self, *clauses):
        self.order_by_clauses.extend(clauses)
        return self

    def paginate(self, **kwargs):
        self.paginate_kwargs = kwargs
        return 'a page'

    def rendered_clauses(self):
        return [str(clause) for clause in self.order_by_clauses]


class TestPaginateAlwaysOrders(TestCase):

    def test_orders_by_primary_key_when_no_order_by_is_requested(self):
        # The regression: this is the path every default listing takes.
        query = _RecordingQuery()

        paginate(_Widget, PaginationParameters(1, 25, None, 'asc'), query)

        self.assertTrue(
            any('test_paginate_widget.widget_id' in clause
                for clause in query.rendered_clauses()),
            'paginate() must order by the primary key even when the caller '
            'asks for no particular order, or pages repeat and skip rows'
        )

    def test_requested_order_comes_before_the_primary_key(self):
        query = _RecordingQuery()

        paginate(_Widget, PaginationParameters(1, 25, 'name', 'asc'), query)

        clauses = query.rendered_clauses()
        self.assertIn('test_paginate_widget.name ASC', clauses[0])
        self.assertIn('test_paginate_widget.widget_id', clauses[-1])

    def test_unknown_order_by_field_still_yields_a_stable_order(self):
        # `hasattr` rejects the field, so the caller's sort is dropped — the
        # query must not be left unordered as a result.
        query = _RecordingQuery()

        paginate(_Widget, PaginationParameters(1, 25, 'nonexistent', 'asc'), query)

        self.assertTrue(
            any('test_paginate_widget.widget_id' in clause
                for clause in query.rendered_clauses())
        )

    def test_pagination_parameters_are_passed_through(self):
        query = _RecordingQuery()

        paginate(_Widget, PaginationParameters(3, 25, None, 'asc'), query)

        self.assertEqual(3, query.paginate_kwargs['page'])
        self.assertEqual(25, query.paginate_kwargs['per_page'])
        self.assertFalse(query.paginate_kwargs['error_out'])
