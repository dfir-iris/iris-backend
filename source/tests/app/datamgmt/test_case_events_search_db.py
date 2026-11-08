#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for the page query of the case timeline search.

The session is patched out; what is tested is the SQL the page compiles
to — whether descriptions and raw data are cut to a preview.
"""

from unittest import TestCase
from unittest.mock import patch

from sqlalchemy import true
from sqlalchemy.dialects import postgresql

from app.datamgmt.case import case_events_search_db as module


def _page_sql(**kwargs):
    with patch.object(module.db, 'session') as session:
        session.execute.return_value.all.return_value = []
        module.events_search_page(true(), **kwargs)
    statement = session.execute.call_args.args[0]
    return str(statement.compile(dialect=postgresql.dialect())).lower()


class TestSearchPageText(TestCase):

    def test_list_pages_cut_descriptions_and_raw_data(self):
        sql = _page_sql()
        self.assertIn('left(cases_events.event_content', sql)
        self.assertIn('left(cases_events.event_raw', sql)

    def test_full_text_pages_return_them_whole(self):
        sql = _page_sql(full_text=True)
        self.assertNotIn('left(', sql)
        self.assertIn('cases_events.event_content as event_content', sql)
