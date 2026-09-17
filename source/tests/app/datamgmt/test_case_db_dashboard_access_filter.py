#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""The dashboard case/review lists must filter on effective case access.

`list_user_cases` and `list_user_reviews` back
`/api/v2/dashboard/cases/list` and `/api/v2/dashboard/reviews/list`. They
used to select on `Cases.owner_id` / `Cases.reviewer_id` alone.

Neither column grants access — access is resolved purely from
`UserCaseEffectiveAccess` — and revoking a user's access to a case does
not clear them. So an assignment made before a revocation kept surfacing
the case to a user who could no longer open it: `reviews/list` leaks the
case id and name, and `cases/list` dumps `CaseDetailsSchema`, i.e. the
full record including description, closing note, custom attributes and
modification history.

These tests compile the queries and assert the access join is in the SQL.
No DB connection is made — `str(query)` renders without one.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..', 'source'))

os.environ.setdefault('POSTGRES_SERVER', 'localhost')

from app import app

from app.models.cases import Cases
from app.datamgmt.case.case_db import _cases_visible_to


def _ctx():
    ctx = app.app_context()
    ctx.push()
    return ctx


_APP_CTX = _ctx()

USER_ID = 5


def _sql(query):
    return ' '.join(str(query).split())


class TestCasesVisibleToSubquery(unittest.TestCase):

    def test_selects_case_ids_from_effective_access(self):
        sql = _sql(_cases_visible_to(USER_ID))
        self.assertIn('user_case_effective_access.case_id', sql)
        self.assertIn('FROM user_case_effective_access', sql)

    def test_filters_on_the_user(self):
        self.assertIn('user_case_effective_access.user_id =', _sql(_cases_visible_to(USER_ID)))

    def test_drops_deny_all(self):
        self.assertIn('user_case_effective_access.access_level !=',
                      _sql(_cases_visible_to(USER_ID)))


class TestDashboardListsAreAccessFiltered(unittest.TestCase):
    """The filter has to be applied by the list queries, not just available."""

    def _assert_access_filtered(self, query):
        sql = _sql(query)
        self.assertIn('IN (SELECT user_case_effective_access.case_id', sql)
        self.assertIn('user_case_effective_access.access_level !=', sql)

    def test_list_user_cases_open_only_is_filtered(self):
        # Mirrors list_user_cases(user, show_all=False).
        query = Cases.query.filter(
            Cases.owner_id == USER_ID,
            Cases.case_id.in_(_cases_visible_to(USER_ID)),
            Cases.close_date == None
        )
        self._assert_access_filtered(query)
        self.assertIn('cases.close_date IS NULL', _sql(query))

    def test_list_user_cases_show_all_is_filtered(self):
        # Mirrors list_user_cases(user, show_all=True) — the show_all branch
        # is a separate query and was the easier one to forget.
        query = Cases.query.filter(
            Cases.owner_id == USER_ID,
            Cases.case_id.in_(_cases_visible_to(USER_ID))
        )
        self._assert_access_filtered(query)

    def test_both_list_user_cases_branches_apply_the_filter(self):
        # Guards against the filter being added to one branch only, by
        # reading the real function source rather than a reconstruction.
        import inspect
        from app.datamgmt.case import case_db
        source = inspect.getsource(case_db.list_user_cases)
        self.assertEqual(2, source.count('_cases_visible_to'),
                         'both the show_all and open-only branches must filter')

    def test_list_user_reviews_applies_the_filter(self):
        import inspect
        from app.datamgmt.case import case_db
        source = inspect.getsource(case_db.list_user_reviews)
        self.assertIn('_cases_visible_to', source)


if __name__ == '__main__':
    unittest.main()
