#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for the alert search compiler.

The compiler turns a parsed expression into one SQLAlchemy clause, and
most of what can go wrong is invisible until the query runs against
Postgres — a join where an EXISTS was meant, a `NOT` that swallows the
NULL rows, a literal `%` that became a wildcard. So these tests compile
the clause against the Postgres dialect and assert on the SQL text and
the bound parameters rather than on the object graph.

Value resolution reads the database, which these tests replace with a
canned session: `_FakeSession` answers a `query(...)` by the key of the
column being selected, and hands back a queue of result sets so the
exact-then-substring fallback can be exercised.
"""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from sqlalchemy import and_
from sqlalchemy.dialects import postgresql

from app.datamgmt.filtering import combine_conditions
from app.datamgmt.lucene import query_compiler
from app.datamgmt.lucene.query_compiler import compile_alert_query
from app.models.alerts import Alert
from app.models.errors import SearchQueryError

#: What `post_init` seeds. `is:open` resolves terminal statuses by name,
#: so the ids here are deliberately not the ones a fixture would pick.
_STATUS_ROWS = [
    (1, 'Unspecified'), (2, 'New'), (3, 'Assigned'), (4, 'In progress'),
    (5, 'Pending'), (6, 'Closed'), (7, 'Merged'), (8, 'Escalated'),
]


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
    """Answers the compiler's lookups without a database.

    `rows_by_key` maps the selected column's key to a *queue* of result
    sets: the first call gets the first entry, and the last entry repeats
    once the queue runs dry. That is what makes the exact-match-then-
    substring fallback testable — `status:progress` misses on the first
    query and hits on the second.
    """

    def __init__(self, rows_by_key=None, status_rows=_STATUS_ROWS):
        self._queues = {key: list(value) for key, value in (rows_by_key or {}).items()}
        self._status_rows = list(status_rows)
        self.selected = []

    def query(self, *entities):
        self.selected.append([getattr(entity, 'key', None) for entity in entities])
        if len(entities) > 1:
            return _FakeQuery(self._status_rows)

        queue = self._queues.get(getattr(entities[0], 'key', None), [[]])
        rows = queue[0]
        if len(queue) > 1:
            queue.pop(0)
        return _FakeQuery(rows)


def _compile(query, rows_by_key=None, status_rows=_STATUS_ROWS, user_identifier=None):
    """`(sql, params, session)` for an expression."""
    session = _FakeSession(rows_by_key, status_rows)
    with patch.object(query_compiler, 'db', SimpleNamespace(session=session)):
        clause = compile_alert_query(query, user_identifier)

    compiled = clause.compile(dialect=postgresql.dialect())
    return str(compiled).replace('\n', ' '), dict(compiled.params), session


def _error(test, query, rows_by_key=None, status_rows=_STATUS_ROWS, user_identifier=None):
    session = _FakeSession(rows_by_key, status_rows)
    with test.assertRaises(SearchQueryError) as caught:
        with patch.object(query_compiler, 'db', SimpleNamespace(session=session)):
            compile_alert_query(query, user_identifier)
    return caught.exception


class TestEmptyExpressions(TestCase):

    def test_none_compiles_to_no_clause(self):
        # "No query" is not "match nothing" — the caller must be able to
        # tell the difference, or an empty search bar would empty the list.
        self.assertIsNone(compile_alert_query(None))

    def test_blank_compiles_to_no_clause(self):
        self.assertIsNone(compile_alert_query('   '))


class TestFieldlessTerms(TestCase):

    def test_bare_term_searches_every_default_column(self):
        sql, params, _ = _compile('ransom')
        self.assertEqual(6, sql.count('ILIKE'))
        self.assertEqual(6, sql.count('OR') + 1)
        self.assertEqual({'%ransom%'}, set(params.values()))

    def test_bare_term_covers_title_and_source(self):
        sql, _, _ = _compile('ransom')
        self.assertIn('alerts.alert_title ILIKE', sql)
        self.assertIn('alerts.alert_source ILIKE', sql)

    def test_two_bare_terms_are_anded(self):
        # Juxtaposition is AND, so a second word narrows rather than widens.
        sql, params, _ = _compile('ransom note')
        self.assertIn(') AND (', sql)
        self.assertEqual({'%ransom%', '%note%'}, set(params.values()))

    def test_range_without_a_field_is_refused(self):
        error = _error(self, '[1 TO 5]')
        self.assertIn('needs a field', error.get_message())


class TestTextFields(TestCase):

    def test_alias_resolves_to_its_column(self):
        sql, _, _ = _compile('title:ransom')
        self.assertIn('alerts.alert_title ILIKE', sql)

    def test_synonym_resolves_to_the_same_column(self):
        sql, _, _ = _compile('desc:ransom')
        self.assertIn('alerts.alert_description ILIKE', sql)

    def test_plain_term_matches_a_substring(self):
        _, params, _ = _compile('title:ransom')
        self.assertEqual('%ransom%', params['alert_title_1'])

    def test_wildcard_anchors_the_pattern(self):
        # `*.corp.local` means "ends with", not "contains something that
        # ends with" — wrapping it in %…% would make the anchor a lie.
        _, params, _ = _compile('title:*.corp.local')
        self.assertEqual('%.corp.local', params['alert_title_1'])

    def test_trailing_wildcard_is_a_prefix_match(self):
        _, params, _ = _compile('tag:phish*')
        self.assertEqual('phish%', params['alert_tags_1'])

    def test_question_mark_is_a_single_character_wildcard(self):
        _, params, _ = _compile('title:h?st')
        self.assertEqual('h_st', params['alert_title_1'])

    def test_literal_percent_is_escaped(self):
        # Regression guard: without escaping, `title:100%` would match
        # every alert whose title starts with 100 — the analyst typed a
        # percent sign, not a wildcard.
        _, params, _ = _compile('title:100%')
        self.assertEqual('%100\\%%', params['alert_title_1'])

    def test_literal_underscore_is_escaped(self):
        _, params, _ = _compile('title:a_b')
        self.assertEqual('%a\\_b%', params['alert_title_1'])

    def test_pattern_carries_an_escape_clause(self):
        sql, _, _ = _compile('title:100%')
        self.assertIn("ESCAPE", sql.upper())

    def test_quoted_phrase_keeps_the_asterisk_literal(self):
        # Quoting is how you search for an asterisk, so a quoted value
        # must not grow wildcards.
        _, params, _ = _compile('title:"exact * phrase"')
        self.assertEqual('%exact * phrase%', params['alert_title_1'])

    def test_escaped_asterisk_is_literal(self):
        _, params, _ = _compile('title:foo\\*bar')
        self.assertEqual('%foo*bar%', params['alert_title_1'])

    def test_text_field_refuses_a_comparison(self):
        error = _error(self, 'title:>x')
        self.assertIn('cannot be compared', error.get_message())


class TestIdentifierFields(TestCase):

    def test_id_matches_exactly(self):
        sql, params, _ = _compile('id:42')
        self.assertIn('alerts.alert_id = ', sql)
        self.assertEqual(42, params['alert_id_1'])

    def test_id_takes_a_number(self):
        error = _error(self, 'id:abc')
        self.assertIn('takes a number', error.get_message())

    def test_uuid_matches_exactly(self):
        sql, params, _ = _compile('uuid:6a5e9a24-1e09-4d31-8e2a-5c9b3a4d6f11')
        self.assertIn('alerts.alert_uuid = ', sql)
        self.assertEqual('6a5e9a24-1e09-4d31-8e2a-5c9b3a4d6f11', params['alert_uuid_1'])

    def test_malformed_uuid_is_refused(self):
        # Passing a non-UUID to a uuid column makes Postgres raise at
        # execution time, well past any try/except around the query build.
        error = _error(self, 'uuid:nope')
        self.assertIn('is not a UUID', error.get_message())

    def test_uuid_prefix_with_a_wildcard_is_a_pattern(self):
        # A uuid column cannot take an ILIKE, so the pattern form casts to
        # text first — without that, Postgres raises at execution time.
        sql, params, _ = _compile('uuid:6a5e9a24*')
        self.assertIn('ILIKE', sql)
        self.assertIn('CAST', sql.upper())
        self.assertIn('6a5e9a24%', params.values())


class TestEnumResolution(TestCase):

    def test_status_name_resolves_to_an_id(self):
        sql, params, _ = _compile('status:Closed', {'status_id': [[(6,)]]})
        self.assertIn('alerts.alert_status_id = ', sql)
        self.assertEqual(6, params['alert_status_id_1'])

    def test_status_lookup_is_case_insensitive(self):
        _, params, _ = _compile('status:closed', {'status_id': [[(6,)]]})
        self.assertEqual(6, params['alert_status_id_1'])

    def test_partial_name_falls_back_to_a_substring_match(self):
        # `status:progress` should find "In progress" rather than telling
        # an analyst three keystrokes from the answer that it does not exist.
        sql, params, session = _compile('status:progress',
                                        {'status_id': [[], [(4,)]]})
        self.assertIn('alerts.alert_status_id = ', sql)
        self.assertEqual(4, params['alert_status_id_1'])
        self.assertEqual(2, len(session.selected))

    def test_ambiguous_name_compiles_to_an_in_clause(self):
        sql, params, _ = _compile('status:pend', {'status_id': [[], [(5,), (2,)]]})
        self.assertIn('IN', sql)
        self.assertEqual([5, 2], params['alert_status_id_1'])

    def test_value_group_expands_to_an_or(self):
        sql, _, _ = _compile('status:(New OR Closed)',
                             {'status_id': [[(2,)], [(6,)]]})
        self.assertIn(' OR ', sql)

    def test_none_matches_rows_with_no_value(self):
        sql, _, _ = _compile('classification:none')
        self.assertIn('alerts.alert_classification_id IS NULL', sql)

    def test_unresolvable_value_is_refused(self):
        error = _error(self, 'status:Nope', {'status_id': [[]], 'status_name': [[]]})
        self.assertIn("No status matches 'Nope'", error.get_message())

    def test_unresolvable_value_suggests_a_real_one(self):
        error = _error(self, 'status:Clsoed',
                       {'status_id': [[]], 'status_name': [[('Closed',), ('New',)]]})
        self.assertIn("did you mean 'Closed'", error.get_message())

    def test_unknown_customer_never_names_a_real_one(self):
        # A suggestion here would let anyone turn a typo into a list of
        # the client names on the deployment, including tenants they have
        # no access to. The message stays deliberately unhelpful.
        error = _error(self, 'customer:Nope',
                       {'client_id': [[]], 'name': [[('Contoso Ltd',)]]})
        self.assertIn("No customer matches 'Nope'", error.get_message())
        self.assertNotIn('Contoso', error.get_message())

    def test_lookup_is_cached_within_one_expression(self):
        _, _, session = _compile('status:Closed status:Closed', {'status_id': [[(6,)]]})
        self.assertEqual(1, len(session.selected))


class TestOrderedEnums(TestCase):

    def test_severity_comparison_resolves_the_bound(self):
        sql, params, _ = _compile('severity:>=High', {'severity_id': [[(3,)]]})
        self.assertIn('alerts.alert_severity_id >= ', sql)
        self.assertEqual(3, params['alert_severity_id_1'])

    def test_severity_range_becomes_two_comparisons(self):
        sql, _, _ = _compile('severity:[Low TO High]',
                             {'severity_id': [[(1,)], [(4,)]]})
        self.assertIn('>=', sql)
        self.assertIn('<=', sql)

    def test_status_has_no_order_so_comparison_is_refused(self):
        # Status ids are insertion order. Answering `status:>=Closed` by
        # row id would be a wrong answer delivered confidently.
        error = _error(self, 'status:>=Closed', {'status_id': [[(6,)]]})
        self.assertIn('has no order', error.get_message())

    def test_ambiguous_comparison_bound_is_refused(self):
        error = _error(self, 'severity:>=Vague', {'severity_id': [[(1,), (2,)]]})
        self.assertIn('matches several severity values', error.get_message())


class TestOwner(TestCase):

    def test_me_resolves_to_the_calling_user(self):
        sql, params, _ = _compile('owner:me', user_identifier=42)
        self.assertIn('alerts.alert_owner_id = ', sql)
        self.assertEqual(42, params['alert_owner_id_1'])

    def test_me_without_a_caller_is_refused(self):
        error = _error(self, 'owner:me')
        self.assertIn('needs an authenticated user', error.get_message())

    def test_none_matches_unassigned_alerts(self):
        sql, _, _ = _compile('owner:none')
        self.assertIn('alerts.alert_owner_id IS NULL', sql)

    def test_login_resolves_to_a_user_id(self):
        sql, params, _ = _compile('owner:jdoe', {'id': [[(7,)]]})
        self.assertIn('alerts.alert_owner_id = ', sql)
        self.assertEqual(7, params['alert_owner_id_1'])

    def test_assignee_is_the_same_field(self):
        sql, _, _ = _compile('assignee:none')
        self.assertIn('alerts.alert_owner_id IS NULL', sql)


class TestMacros(TestCase):

    def test_open_excludes_terminal_statuses_by_name(self):
        # Statuses are seeded by name and editable, so the macro resolves
        # them every time instead of trusting ids a deployment may not have.
        sql, params, _ = _compile('is:open')
        self.assertIn('NOT IN', sql)
        self.assertEqual([6, 7, 8], params['alert_status_id_1'])

    def test_closed_includes_terminal_statuses(self):
        sql, params, _ = _compile('is:closed')
        self.assertIn('IN', sql)
        self.assertNotIn('NOT IN', sql)
        self.assertEqual([6, 7, 8], params['alert_status_id_1'])

    def test_open_matches_everything_when_no_terminal_status_exists(self):
        # A deployment that renamed every status must still show a queue,
        # not an empty `NOT IN ()`.
        sql, _, _ = _compile('is:open', status_rows=[(1, 'Triage')])
        self.assertEqual('true', sql.strip())

    def test_closed_matches_nothing_when_no_terminal_status_exists(self):
        sql, _, _ = _compile('is:closed', status_rows=[(1, 'Triage')])
        self.assertEqual('false', sql.strip())

    def test_unassigned_is_a_null_owner(self):
        sql, _, _ = _compile('is:unassigned')
        self.assertIn('alerts.alert_owner_id IS NULL', sql)

    def test_assigned_is_a_non_null_owner(self):
        sql, _, _ = _compile('is:assigned')
        self.assertIn('alerts.alert_owner_id IS NOT NULL', sql)

    def test_resolved_is_a_non_null_resolution(self):
        sql, _, _ = _compile('is:resolved')
        self.assertIn('alerts.alert_resolution_status_id IS NOT NULL', sql)

    def test_clustered_is_an_exists(self):
        sql, _, _ = _compile('is:clustered')
        self.assertTrue(sql.startswith('EXISTS'))

    def test_orphan_is_a_negated_exists(self):
        sql, _, _ = _compile('is:orphan')
        self.assertIn('NOT (EXISTS', sql)

    def test_in_case_is_an_exists_over_cases(self):
        sql, _, _ = _compile('is:in_case')
        self.assertTrue(sql.startswith('EXISTS'))
        self.assertIn('alert_case_association', sql)

    def test_unknown_state_is_refused_with_a_suggestion(self):
        error = _error(self, 'is:opne')
        self.assertIn("Unknown state 'is:opne'", error.get_message())
        self.assertIn("is:open", error.get_message())


class TestDates(TestCase):

    def test_bare_date_covers_the_whole_day(self):
        # "created:2026-07-15" means that Wednesday, not the instant it
        # began — the same correction the grid's date filters apply.
        sql, params, _ = _compile('created:2026-07-15')
        self.assertIn('>=', sql)
        self.assertIn('<=', sql)
        self.assertEqual(0, params['alert_creation_time_1'].hour)
        self.assertEqual(23, params['alert_creation_time_2'].hour)
        self.assertEqual(999999, params['alert_creation_time_2'].microsecond)

    def test_datetime_is_an_instant(self):
        sql, params, _ = _compile('created:2026-07-15T09:30:00')
        self.assertIn('alerts.alert_creation_time = ', sql)
        self.assertEqual(9, params['alert_creation_time_1'].hour)

    def test_relative_date_compiles_to_an_instant(self):
        # A rolling window is the point: a saved filter saying
        # `created:>now-24h` must mean the last day whenever it is opened.
        sql, params, _ = _compile('created:>now-24h')
        self.assertIn('alerts.alert_creation_time > ', sql)
        bound = params['alert_creation_time_1']
        self.assertIsNotNone(bound)

    def test_relative_window_is_not_empty(self):
        # Regression: computing both ends of a `now-…` bound with two
        # calls to utcnow() produced a window microseconds wide that
        # matched nothing.
        _, params, _ = _compile('created:now-24h')
        self.assertEqual(1, len(params))

    def test_inclusive_range_uses_both_inclusive_operators(self):
        sql, params, _ = _compile('created:[2026-01-01 TO 2026-01-31]')
        self.assertIn('>=', sql)
        self.assertIn('<=', sql)
        self.assertEqual(23, params['alert_creation_time_2'].hour)

    def test_exclusive_range_uses_strict_operators(self):
        sql, _, _ = _compile('created:{2026-01-01 TO 2026-01-31}')
        self.assertIn('> ', sql)
        self.assertIn('< ', sql)
        self.assertNotIn('>=', sql)
        self.assertNotIn('<=', sql)

    def test_open_ended_range_is_one_comparison(self):
        sql, params, _ = _compile('created:[2026-01-01 TO *]')
        self.assertNotIn('AND', sql)
        self.assertEqual(1, len(params))

    def test_lower_bound_on_a_bare_date_starts_at_midnight(self):
        _, params, _ = _compile('created:>=2026-01-15')
        self.assertEqual(0, params['alert_creation_time_1'].hour)

    def test_upper_bound_on_a_bare_date_ends_at_midnight_minus_one(self):
        _, params, _ = _compile('created:<=2026-01-15')
        self.assertEqual(23, params['alert_creation_time_1'].hour)

    def test_event_time_alias_targets_the_source_event_column(self):
        sql, _, _ = _compile('seen:2026-07-15')
        self.assertIn('alerts.alert_source_event_time', sql)

    def test_unparsable_date_is_refused(self):
        error = _error(self, 'created:notadate')
        self.assertIn('is not a date', error.get_message())


class TestRelationships(TestCase):

    def test_asset_compiles_to_an_exists(self):
        # Not a join: under an inner join, `asset:HOST-1 OR title:foo`
        # silently drops every alert with no assets at all, because the
        # join eliminated the row before the OR was evaluated.
        sql, _, _ = _compile('asset:HOST-1')
        self.assertTrue(sql.startswith('EXISTS'))
        self.assertNotIn('JOIN', sql.upper())

    def test_asset_matches_a_substring_of_the_name(self):
        _, params, _ = _compile('asset:HOST-1')
        self.assertEqual('%HOST-1%', params['asset_name_1'])

    def test_asset_ip_targets_the_ip_column(self):
        sql, _, _ = _compile('asset_ip:10.0.0.1')
        self.assertIn('case_assets.asset_ip ILIKE', sql)

    def test_ioc_compiles_to_an_exists(self):
        sql, _, _ = _compile('ioc:evil.com')
        self.assertTrue(sql.startswith('EXISTS'))
        self.assertIn('ioc.ioc_value ILIKE', sql)

    def test_comment_compiles_to_an_exists(self):
        sql, _, _ = _compile('comment:"false positive"')
        self.assertTrue(sql.startswith('EXISTS'))
        self.assertIn('comments.comment_text ILIKE', sql)

    def test_relationship_under_or_keeps_both_sides_reachable(self):
        # The shape that a join would break: an alert with no assets must
        # still be able to match on its title.
        sql, _, _ = _compile('asset:HOST-1 OR title:ransom')
        self.assertIn(' OR ', sql)
        self.assertIn('EXISTS', sql)
        self.assertIn('alerts.alert_title ILIKE', sql)

    def test_relationship_refuses_a_comparison(self):
        error = _error(self, 'asset:>x')
        self.assertIn('cannot be compared', error.get_message())


class TestMembership(TestCase):

    def test_case_identifier_compiles_to_an_exists(self):
        sql, params, _ = _compile('case:12')
        self.assertTrue(sql.startswith('EXISTS'))
        self.assertEqual(12, params['case_id_1'])

    def test_case_none_is_a_negated_exists(self):
        sql, _, _ = _compile('case:none')
        self.assertIn('NOT (EXISTS', sql)

    def test_cluster_identifier_compiles_to_an_exists(self):
        sql, params, _ = _compile('cluster:7')
        self.assertTrue(sql.startswith('EXISTS'))
        self.assertEqual(7, params['cluster_id_1'])

    def test_membership_takes_a_number(self):
        error = _error(self, 'case:abc')
        self.assertIn('takes a number', error.get_message())


class TestJsonPaths(TestCase):

    def test_context_path_reads_the_context_document(self):
        sql, params, _ = _compile('context.rule_name:"brute force"')
        self.assertIn('alerts.alert_context ->> ', sql)
        self.assertEqual('rule_name', params['alert_context_1'])

    def test_context_value_matches_a_substring(self):
        _, params, _ = _compile('context.rule_name:brute')
        self.assertIn('%brute%', params.values())

    def test_raw_path_reads_the_source_event(self):
        sql, _, _ = _compile('raw.event_id:4625')
        self.assertIn('alerts.alert_source_content', sql)

    def test_nested_path_walks_the_document(self):
        sql, params, _ = _compile('context.rule.name:brute')
        self.assertIn('alerts.alert_context -> ', sql)
        self.assertEqual('rule', params['alert_context_1'])

    def test_json_comparison_casts_to_numeric(self):
        sql, _, _ = _compile('raw.score:>80')
        self.assertIn('NUMERIC', sql.upper())

    def test_path_is_required(self):
        error = _error(self, 'context:x')
        self.assertIn('needs a path', error.get_message())


class TestNegation(TestCase):

    def test_negation_is_is_not_true(self):
        # Postgres three-valued logic: under a plain `NOT`, an unowned
        # alert fails `-owner:jdoe` because `NULL = 7` is NULL — hiding
        # exactly the unassigned alerts an analyst is hunting for.
        sql, _, _ = _compile('-owner:jdoe', {'id': [[(7,)]]})
        self.assertIn('IS NOT true', sql)

    def test_not_keyword_negates_the_same_way(self):
        sql, _, _ = _compile('NOT title:ransom')
        self.assertIn('IS NOT true', sql)

    def test_negated_group_is_negated_once(self):
        sql, _, _ = _compile('-(title:a OR title:b)')
        self.assertEqual(1, sql.count('IS NOT true'))
        self.assertIn(' OR ', sql)


class TestRawColumnEscapeHatch(TestCase):

    def test_literal_alert_column_resolves(self):
        # Anything the grid can filter on stays reachable from the bar
        # even before it earns a friendly alias.
        sql, _, _ = _compile('alert_source_link:https://foo')
        self.assertIn('alerts.alert_source_link ILIKE', sql)

    def test_unknown_field_is_refused(self):
        error = _error(self, 'bogus:1')
        self.assertIn("Unknown field 'bogus'", error.get_message())

    def test_near_miss_suggests_an_alias(self):
        error = _error(self, 'titel:x')
        self.assertIn("did you mean 'title'", error.get_message())

    def test_non_alert_attribute_is_not_reachable(self):
        # The escape hatch is `Alert` columns, not arbitrary attributes.
        error = _error(self, 'query:x')
        self.assertIn('Unknown field', error.get_message())


class TestTenancyIsolation(TestCase):
    """The compiled clause is one conjunct, and that is a security property.

    The caller appends it to the condition list it already built and
    combines with `and_`, so the tenancy predicate stays outside it. These
    tests pin the shape that makes that true — an `OR` an analyst writes
    must never escape its parentheses and widen what they can see.
    """

    def test_or_of_customers_stays_parenthesised_under_a_tenancy_filter(self):
        session = _FakeSession({'client_id': [[(1,)], [(2,)]]})
        with patch.object(query_compiler, 'db', SimpleNamespace(session=session)):
            clause = compile_alert_query('customer:ACME OR customer:Globex')

        combined = and_(Alert.alert_customer_id.in_([9, 10]), clause)
        sql = str(combined.compile(dialect=postgresql.dialect())).replace('\n', ' ')

        self.assertIn('IN (', sql)
        self.assertIn(' AND (', sql)
        self.assertTrue(sql.index(' AND (') < sql.index(' OR '),
                        'the user OR must sit inside the tenancy AND, not beside it')

    def test_combine_conditions_keeps_a_user_or_grouped(self):
        # The real call path: `_alert_filter_conditions` appends the
        # compiled clause to its condition list and calls this helper. If
        # the OR flattened into the list, every row would match.
        session = _FakeSession()
        with patch.object(query_compiler, 'db', SimpleNamespace(session=session)):
            clause = compile_alert_query('title:a OR title:b')

        combined = combine_conditions([Alert.alert_customer_id.in_([9]), clause], 'and')
        sql = str(combined.compile(dialect=postgresql.dialect())).replace('\n', ' ')

        self.assertIn(' AND (', sql)
        self.assertTrue(sql.index(' AND (') < sql.index(' OR '))


class TestRealisticExpressions(TestCase):

    def test_open_alerts_assigned_to_me(self):
        sql, _, _ = _compile('is:open owner:me', user_identifier=42)
        self.assertIn('NOT IN', sql)
        self.assertIn('alerts.alert_owner_id = ', sql)
        self.assertIn(' AND ', sql)

    def test_high_severity_not_closed(self):
        sql, _, _ = _compile('severity:>=High AND -status:Closed',
                             {'severity_id': [[(3,)]], 'status_id': [[(6,)]]})
        self.assertIn('alerts.alert_severity_id >= ', sql)
        self.assertIn('IS NOT true', sql)

    def test_domain_assets_in_the_last_day(self):
        sql, params, _ = _compile('asset:*.corp.local created:>now-24h')
        self.assertIn('EXISTS', sql)
        self.assertIn('alerts.alert_creation_time > ', sql)
        self.assertIn('%.corp.local', params.values())

    def test_two_sources_and_a_tag(self):
        sql, params, _ = _compile('(source:crowdstrike OR source:sentinel) tag:phishing')
        self.assertIn(' OR ', sql)
        self.assertIn('alerts.alert_tags ILIKE', sql)
        self.assertIn('%phishing%', params.values())
