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

"""Regression tests for StableQuery.

Paging a 104-customer table 25 at a time used to return 104 rows containing
only 75 distinct customers: 29 were unreachable, and two consecutive pages
consisted entirely of rows already served earlier. The cause was a LIMIT/OFFSET
page over a query with no ORDER BY, which PostgreSQL is free to arrange
differently on every execution.

Note what these tests deliberately do NOT do: supply `order_by`. A test that
passed one would have gone green against the broken code, which is precisely
how the defect survived into a release.
"""

from unittest import TestCase

from sqlalchemy import Column
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy.orm import declarative_base

from app.db import StableQuery

_Base = declarative_base()


class _Widget(_Base):
    __tablename__ = 'test_widget'

    widget_id = Column(Integer, primary_key=True)
    name = Column(String)


class _Sprocket(_Base):
    __tablename__ = 'test_sprocket'

    sprocket_id = Column(Integer, primary_key=True)


class TestStableQueryAppendsPrimaryKey(TestCase):

    def test_unordered_entity_query_is_given_an_order(self):
        # The regression. Without this the query has no ORDER BY at all and
        # every page is a fresh roll of the dice.
        statement = str(StableQuery([_Widget])._with_deterministic_order())

        self.assertIn('ORDER BY', statement)
        self.assertIn('test_widget.widget_id', statement)

    def test_existing_order_is_kept_and_the_key_is_appended(self):
        # The caller's sort still wins; the primary key only breaks its ties.
        query = StableQuery([_Widget]).order_by(_Widget.name)

        statement = str(query._with_deterministic_order())

        self.assertIn('ORDER BY test_widget.name, test_widget.widget_id', statement)

    def test_descending_order_is_not_disturbed(self):
        query = StableQuery([_Widget]).order_by(_Widget.name.desc())

        statement = str(query._with_deterministic_order())

        self.assertIn('test_widget.name DESC', statement)
        self.assertIn('test_widget.widget_id', statement)


class TestStableQueryLeavesUnsafeQueriesAlone(TestCase):
    """The guard that makes this safe to apply to every query in the app.

    Appending a column that is not in the select list raises "ORDER BY
    expressions must appear in select list" under SELECT DISTINCT — a runtime
    500, not a test failure. Restricting the rewrite to queries that select one
    whole entity avoids it by construction, because SELECT model.* always
    contains the primary key.
    """

    def test_a_column_query_is_left_untouched(self):
        # Reports _Widget as its entity but does not select widget_id, so
        # ordering by it would be unsafe under DISTINCT.
        statement = str(StableQuery([_Widget.name])._with_deterministic_order())

        self.assertNotIn('ORDER BY', statement)

    def test_a_multi_entity_query_is_left_untouched(self):
        statement = str(StableQuery([_Widget, _Sprocket])._with_deterministic_order())

        self.assertNotIn('ORDER BY', statement)

    def test_a_distinct_entity_query_is_still_safe_to_order(self):
        # SELECT DISTINCT test_widget.* already contains the primary key.
        statement = str(StableQuery([_Widget]).distinct()._with_deterministic_order())

        self.assertIn('DISTINCT', statement)
        self.assertIn('test_widget.widget_id', statement)
