#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Advanced case filters must not join a table the sort already joined.

`build_filter_case_query` joins `User` to order by owner or opened_by,
`Client` for quick search and the customer sort, and `CaseState` for the
state sort. The advanced-filter block in `get_filtered_cases` then joined
the same tables for its `owner` / `customer` / `state` fields.

Two joins of one table under one name is a hard Postgres error, so
filtering on a user and then clicking a column header returned a 500:

    psycopg2.errors.DuplicateAlias:
        table name "user" specified more than once

The advanced block now aliases every join it makes. These tests compile
the query and assert that no join target appears twice. No DB connection
is made — `str(query)` renders without one.
"""

import os
import re
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..', 'source'))

os.environ.setdefault('POSTGRES_SERVER', 'localhost')

from app import app

from app.datamgmt.manage.manage_cases_db import get_filtered_cases
from app.models.pagination_parameters import PaginationParameters


def _ctx():
    ctx = app.app_context()
    ctx.push()
    return ctx


_APP_CTX = _ctx()

USER_ID = 5

_JOIN = re.compile(r'\bJOIN\s+("?\w+"?)(?:\s+AS\s+(\w+))?', re.IGNORECASE)


def _sql(current_user_id=USER_ID, sort_by=None, **kwargs):
    """Render the SQL `get_filtered_cases` would execute.

    Two things reach the database and neither is under test:
    `user_list_cases_view` runs the access lookup up front, and `paginate`
    executes the result. `paginate` is reached through the module-level
    `Query` name in `app.db.StableQuery.paginate`, so replacing that with
    an identity hands back the fully built query instead.
    """
    pagination_parameters = PaginationParameters(1, 25, sort_by, 'asc')
    with patch('app.datamgmt.manage.manage_cases_db.user_list_cases_view', return_value=[1, 2, 3]), \
            patch('app.db.Query.paginate', lambda self, **_: self):
        query = get_filtered_cases(current_user_id, pagination_parameters, **kwargs)
    return ' '.join(str(query).split())


def _join_targets(sql):
    """Every join target, named as Postgres sees it — the alias, or the table."""
    return [(alias or table).strip('"') for table, alias in _JOIN.findall(sql)]


def _owner_filter(value='alice'):
    return [{'fieldId': 'owner', 'operation': 'equals', 'value': value}]


class TestCaseFilterJoinAliases(unittest.TestCase):

    def assert_no_duplicate_join(self, sql):
        targets = _join_targets(sql)
        duplicates = {name for name in targets if targets.count(name) > 1}
        self.assertEqual(set(), duplicates, f'duplicate join target in: {sql}')

    def test_owner_filter_with_owner_sort(self):
        self.assert_no_duplicate_join(_sql(sort_by='owner', advanced_filters=_owner_filter()))

    def test_owner_filter_with_opened_by_sort(self):
        self.assert_no_duplicate_join(_sql(sort_by='opened_by', advanced_filters=_owner_filter()))

    def test_customer_filter_with_customer_sort(self):
        sql = _sql(
            sort_by='customer_name',
            advanced_filters=[{'fieldId': 'customer', 'operation': 'equals', 'value': 'acme'}]
        )
        self.assert_no_duplicate_join(sql)

    def test_customer_filter_with_quick_search(self):
        sql = _sql(
            quick_search='acme',
            advanced_filters=[{'fieldId': 'customer', 'operation': 'contains', 'value': 'acme'}]
        )
        self.assert_no_duplicate_join(sql)

    def test_state_filter_with_state_sort(self):
        sql = _sql(
            sort_by='state',
            advanced_filters=[{'fieldId': 'state', 'operation': 'equals', 'value': 'Open'}]
        )
        self.assert_no_duplicate_join(sql)

    def test_two_conditions_on_the_same_field_join_once(self):
        sql = _sql(advanced_filters=[
            {'fieldId': 'owner', 'operation': 'not', 'value': 'alice'},
            {'fieldId': 'owner', 'operation': 'not', 'value': 'bob'}
        ])
        self.assert_no_duplicate_join(sql)

    def test_every_joined_field_at_once(self):
        sql = _sql(sort_by='owner', quick_search='acme', advanced_filters=[
            {'fieldId': 'owner', 'operation': 'equals', 'value': 'alice'},
            {'fieldId': 'customer', 'operation': 'equals', 'value': 'acme'},
            {'fieldId': 'state', 'operation': 'equals', 'value': 'Open'},
            {'fieldId': 'severity', 'operation': 'equals', 'value': 'High'}
        ])
        self.assert_no_duplicate_join(sql)


class TestCaseFilterConditionsStillApply(unittest.TestCase):
    """Aliasing must not quietly drop the condition it was joined for."""

    def test_owner_condition_is_in_the_where_clause(self):
        sql = _sql(sort_by='owner', advanced_filters=_owner_filter())
        self.assertRegex(sql, r'WHERE.*\buser_\d+\."user"')

    def test_owner_sort_still_orders_on_the_unaliased_join(self):
        sql = _sql(sort_by='owner', advanced_filters=_owner_filter())
        self.assertIn('ORDER BY "user".name ASC', sql)

    def test_customer_condition_is_in_the_where_clause(self):
        sql = _sql(advanced_filters=[{'fieldId': 'customer', 'operation': 'equals', 'value': 'acme'}])
        self.assertRegex(sql, r'WHERE.*\bclient_\d+\.name\b')

    def test_severity_condition_is_in_the_where_clause(self):
        sql = _sql(advanced_filters=[{'fieldId': 'severity', 'operation': 'equals', 'value': 'High'}])
        self.assertRegex(sql, r'WHERE.*\bseverities_\d+\.severity_name\b')


class TestCaseFilterJoinsAreOuter(unittest.TestCase):
    """A case with no owner must survive long enough to be tested.

    An inner join drops rows whose foreign key is NULL before any
    condition runs, so `owner is empty` matched nothing and every negative
    operator silently under-reported.
    """

    def test_owner_is_outer_joined(self):
        sql = _sql(advanced_filters=[{'fieldId': 'owner', 'operation': 'empty', 'value': ''}])
        self.assertRegex(sql, r'LEFT OUTER JOIN "user" AS user_\d+')

    def test_customer_is_outer_joined(self):
        sql = _sql(advanced_filters=[{'fieldId': 'customer', 'operation': 'empty', 'value': ''}])
        self.assertRegex(sql, r'LEFT OUTER JOIN client AS client_\d+')

    def test_state_is_outer_joined(self):
        sql = _sql(advanced_filters=[{'fieldId': 'state', 'operation': 'empty', 'value': ''}])
        self.assertRegex(sql, r'LEFT OUTER JOIN case_state AS case_state_\d+')


if __name__ == '__main__':
    unittest.main()
