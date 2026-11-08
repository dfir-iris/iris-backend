#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for the case timeline search compiler.

Same approach as the alert compiler tests: compile against the Postgres
dialect and assert on the SQL text. The point of most of these is the
shape of the relation predicates — every link table must be reached
through an EXISTS correlated to the outer `cases_events` row, never a
join (which would duplicate rows) and never an uncorrelated subquery
(which would match every event as soon as one event matches).
"""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.datamgmt.lucene import query_compiler
from app.datamgmt.lucene.event_fields import event_field_catalogue
from app.datamgmt.lucene.event_fields import event_field_raw_column
from app.datamgmt.lucene.event_query_compiler import compile_event_query
from app.models.cases import CasesEvent
from app.models.errors import SearchQueryError


class _FakeQuery:

    def __init__(self, rows):
        self._rows = rows

    def filter(self, *args, **kwargs):
        return self

    def limit(self, count):
        return self

    def all(self):
        return self._rows


class _FakeSession:

    def __init__(self, rows=None):
        self._rows = rows or []

    def query(self, *entities):
        return _FakeQuery(self._rows)


def _compile(query, user_identifier=None, rows=None):
    """`(sql, params)` of the clause embedded in a scoped select, as the routes run it."""
    with patch.object(query_compiler, 'db', SimpleNamespace(session=_FakeSession(rows))):
        clause = compile_event_query(query, user_identifier)

    statement = select(CasesEvent.event_id).where(CasesEvent.case_id == 1, clause)
    compiled = statement.compile(dialect=postgresql.dialect())
    return str(compiled).replace('\n', ' '), dict(compiled.params)


def _error(test, query, user_identifier=None):
    with test.assertRaises(SearchQueryError) as caught:
        with patch.object(query_compiler, 'db', SimpleNamespace(session=_FakeSession())):
            compile_event_query(query, user_identifier)
    return caught.exception


def _subqueries(sql):
    return sql.split('EXISTS (')[1:]


class TestEmptyExpressions(TestCase):

    def test_blank_compiles_to_no_clause(self):
        self.assertIsNone(compile_event_query('  '))


class TestFieldlessTerms(TestCase):

    def test_bare_term_searches_every_default_column(self):
        sql, params = _compile('mimikatz')
        self.assertEqual(5, sql.count('ILIKE'))
        for column in ('event_title', 'event_content', 'event_source', 'event_tags', 'event_raw'):
            self.assertIn(f'cases_events.{column} ILIKE', sql)
        self.assertIn('%mimikatz%', params.values())


class TestTextFields(TestCase):

    def test_alias_resolves_to_its_column(self):
        sql, _ = _compile('source:sysmon')
        self.assertIn('cases_events.event_source ILIKE', sql)

    def test_synonym_resolves_like_the_alias(self):
        sql, _ = _compile('desc:lateral')
        self.assertIn('cases_events.event_content ILIKE', sql)

    def test_raw_event_column_is_reachable(self):
        self.assertIsNotNone(event_field_raw_column('event_color'))

    def test_case_id_is_never_reachable(self):
        self.assertIsNone(event_field_raw_column('case_id'))
        error = _error(self, 'case_id:2')
        self.assertIn('case_id', error.get_message())


class TestRelations(TestCase):

    def test_asset_is_a_correlated_exists(self):
        sql, _ = _compile('asset:dc01')
        self.assertNotIn(' JOIN ', sql)
        subqueries = _subqueries(sql)
        self.assertEqual(1, len(subqueries))
        self.assertIn('case_events_assets.event_id = cases_events.event_id', subqueries[0])
        self.assertIn('case_assets.asset_name ILIKE', subqueries[0])
        # Correlated: the outer table must not be re-listed in the subquery.
        self.assertNotIn('FROM case_events_assets, case_assets, cases_events', subqueries[0])

    def test_ioc_reaches_the_ioc_value(self):
        sql, _ = _compile('ioc:"10.0.0.1"')
        self.assertIn('case_events_ioc.event_id = cases_events.event_id', sql)
        self.assertIn('ioc.ioc_value', sql)

    def test_category_reaches_the_category_name(self):
        sql, _ = _compile('category:Persistence')
        self.assertIn('case_events_category.event_id = cases_events.event_id', sql)
        self.assertIn('event_category.name', sql)

    def test_timeline_by_name(self):
        sql, _ = _compile('timeline:"Initial access"')
        self.assertIn('case_event_timelines.event_id = cases_events.event_id', sql)
        self.assertIn('case_timelines.name', sql)

    def test_none_means_no_related_row(self):
        sql, params = _compile('asset:none')
        self.assertIn('NOT (EXISTS', sql)
        self.assertNotIn('%none%', params.values())

    def test_negated_relation_keeps_events_without_links(self):
        sql, _ = _compile('NOT ioc:evil.com')
        self.assertIn('IS NOT true', sql)
        self.assertIn('EXISTS', sql)


class TestDates(TestCase):

    def test_date_range(self):
        sql, _ = _compile('date:[2026-01-01 TO 2026-01-31]')
        self.assertIn('cases_events.event_date >=', sql)
        self.assertIn('cases_events.event_date <=', sql)

    def test_time_is_a_synonym_of_date(self):
        sql, _ = _compile('time:>2026-01-01')
        self.assertIn('cases_events.event_date >', sql)


class TestUsers(TestCase):

    def test_author_me(self):
        sql, params = _compile('author:me', user_identifier=42)
        self.assertIn('cases_events.user_id', sql)
        self.assertIn(42, params.values())


class TestMacros(TestCase):

    def test_flagged(self):
        sql, _ = _compile('is:flagged')
        self.assertIn('cases_events.event_is_flagged IS true', sql)

    def test_root_and_child(self):
        self.assertIn('cases_events.parent_event_id IS NULL', _compile('is:root')[0])
        self.assertIn('cases_events.parent_event_id IS NOT NULL', _compile('is:child')[0])

    def test_parent_looks_for_a_child_row(self):
        sql, _ = _compile('is:parent')
        self.assertIn('child_event.parent_event_id = cases_events.event_id', sql)

    def test_commented(self):
        sql, _ = _compile('is:commented')
        self.assertIn('event_comments.comment_event_id = cases_events.event_id', sql)

    def test_linked_is_assets_or_iocs(self):
        sql, _ = _compile('is:linked')
        self.assertIn('case_events_assets.event_id = cases_events.event_id', sql)
        self.assertIn('case_events_ioc.event_id = cases_events.event_id', sql)

    def test_unknown_macro_is_refused(self):
        error = _error(self, 'is:nope')
        self.assertIsNotNone(error.get_message())


class TestErrors(TestCase):

    def test_unknown_field_suggests_the_closest(self):
        error = _error(self, 'assett:dc01')
        self.assertIn('asset', error.get_message())


class TestCatalogue(TestCase):

    def test_every_entry_has_the_alert_catalogue_shape(self):
        for entry in event_field_catalogue():
            self.assertEqual(
                {'alias', 'synonyms', 'kind', 'description', 'enumerable', 'ordered', 'values'},
                set(entry),
            )

    def test_is_lists_its_values(self):
        entry = next(item for item in event_field_catalogue() if item['alias'] == 'is')
        self.assertIn('flagged', entry['values'])
