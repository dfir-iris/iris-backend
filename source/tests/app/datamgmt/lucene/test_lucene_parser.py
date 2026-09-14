#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for the alert search-bar parser.

The parser is the half of the search bar with no database in it: text in,
AST out. What matters here is that an expression means what it looks like
it means — precedence, negation, quoting — and that anything it cannot
answer is refused with an offset rather than quietly dropped.
"""

from unittest import TestCase

from app.datamgmt.lucene.query_parser import AndNode
from app.datamgmt.lucene.query_parser import ClauseNode
from app.datamgmt.lucene.query_parser import ComparisonValue
from app.datamgmt.lucene.query_parser import lucene_parse
from app.datamgmt.lucene.query_parser import MAX_DEPTH
from app.datamgmt.lucene.query_parser import MAX_NODES
from app.datamgmt.lucene.query_parser import MAX_QUERY_LENGTH
from app.datamgmt.lucene.query_parser import NotNode
from app.datamgmt.lucene.query_parser import OrNode
from app.datamgmt.lucene.query_parser import RangeValue
from app.datamgmt.lucene.query_parser import TermValue
from app.models.errors import SearchQueryError


class TestEmptyExpressions(TestCase):

    def test_none_parses_to_nothing(self):
        self.assertIsNone(lucene_parse(None))

    def test_empty_string_parses_to_nothing(self):
        self.assertIsNone(lucene_parse(''))

    def test_whitespace_only_parses_to_nothing(self):
        # "No query" and "a query that matches nothing" are different
        # answers; blanking the bar must mean the first one.
        self.assertIsNone(lucene_parse('   \t '))


class TestSingleClauses(TestCase):

    def test_bare_word_is_a_field_less_term(self):
        node = lucene_parse('ransomware')

        self.assertIsInstance(node, ClauseNode)
        self.assertIsNone(node.field)
        self.assertEqual('ransomware', node.value.text)
        self.assertFalse(node.value.quoted)

    def test_field_and_value(self):
        node = lucene_parse('title:ransomware')

        self.assertEqual('title', node.field)
        self.assertEqual('ransomware', node.value.text)

    def test_quoted_phrase_keeps_its_spaces(self):
        node = lucene_parse('title:"brute force"')

        self.assertEqual('brute force', node.value.text)
        self.assertTrue(node.value.quoted)

    def test_escaped_quote_stays_inside_the_phrase(self):
        node = lucene_parse('title:"say \\"hello\\""')

        # The escape is preserved rather than resolved: the compiler needs
        # to know a character was escaped to tell a wildcard from a literal.
        self.assertEqual('say \\"hello\\"', node.value.text)

    def test_dotted_field_is_one_token(self):
        node = lucene_parse('context.rule_name:foo')

        self.assertEqual('context.rule_name', node.field)

    def test_position_points_at_the_clause(self):
        node = lucene_parse('    title:foo')

        self.assertEqual(4, node.position)


class TestPrecedence(TestCase):

    def test_juxtaposition_means_and(self):
        node = lucene_parse('crowdstrike phishing')

        self.assertIsInstance(node, AndNode)
        self.assertEqual(2, len(node.children))

    def test_and_binds_tighter_than_or(self):
        # `a OR b c` is `a OR (b AND c)`, not `(a OR b) AND c`. Getting
        # this backwards silently changes what a saved filter matches.
        node = lucene_parse('a OR b c')

        self.assertIsInstance(node, OrNode)
        self.assertIsInstance(node.children[0], ClauseNode)
        self.assertIsInstance(node.children[1], AndNode)

    def test_explicit_and_binds_tighter_than_or(self):
        node = lucene_parse('a OR b AND c')

        self.assertIsInstance(node, OrNode)
        self.assertIsInstance(node.children[1], AndNode)

    def test_parentheses_override_precedence(self):
        node = lucene_parse('(a OR b) c')

        self.assertIsInstance(node, AndNode)
        self.assertIsInstance(node.children[0], OrNode)

    def test_symbolic_operators_are_accepted(self):
        self.assertIsInstance(lucene_parse('a && b'), AndNode)
        self.assertIsInstance(lucene_parse('a || b'), OrNode)

    def test_operator_words_are_uppercase_only(self):
        # `and` is an ordinary English word an analyst may be searching
        # for; only `AND` is the operator.
        node = lucene_parse('command and control')

        self.assertIsInstance(node, AndNode)
        self.assertEqual(3, len(node.children))
        self.assertEqual('and', node.children[1].value.text)


class TestNegation(TestCase):

    def test_not_keyword(self):
        node = lucene_parse('NOT status:Closed')

        self.assertIsInstance(node, NotNode)
        self.assertEqual('status', node.child.field)

    def test_leading_dash(self):
        node = lucene_parse('-status:Closed')

        self.assertIsInstance(node, NotNode)

    def test_bang(self):
        self.assertIsInstance(lucene_parse('!status:Closed'), NotNode)

    def test_dash_inside_a_term_is_literal(self):
        # `HOST-1` is one asset name, not `HOST` minus `1`.
        node = lucene_parse('HOST-1')

        self.assertIsInstance(node, ClauseNode)
        self.assertEqual('HOST-1', node.value.text)

    def test_dash_after_a_field_belongs_to_the_value(self):
        # Negating a clause is `-field:x`; `field:-x` is the value `-x`,
        # which is what makes negative identifiers expressible.
        node = lucene_parse('case:-1')

        self.assertIsInstance(node, ClauseNode)
        self.assertEqual('-1', node.value.text)

    def test_plus_is_accepted_and_means_nothing_extra(self):
        # AND-by-default already makes every clause required; `+` parses
        # so a Lucene habit does not turn into a syntax error.
        node = lucene_parse('+title:foo')

        self.assertIsInstance(node, ClauseNode)
        self.assertEqual('title', node.field)


class TestValueGroups(TestCase):

    def test_explicit_or_inside_a_value_group(self):
        node = lucene_parse('status:(New OR Open)')

        self.assertIsInstance(node, OrNode)
        self.assertEqual(['status', 'status'], [c.field for c in node.children])
        self.assertEqual(['New', 'Open'], [c.value.text for c in node.children])

    def test_juxtaposition_inside_a_value_group_means_or(self):
        # One alert holds one status, so `status:(New Open)` can only
        # sensibly mean "either" — the same reading Kibana gives it.
        node = lucene_parse('status:(New Open)')

        self.assertIsInstance(node, OrNode)

    def test_explicit_and_inside_a_value_group_is_honoured(self):
        node = lucene_parse('tag:(phishing AND external)')

        self.assertIsInstance(node, AndNode)

    def test_negation_inside_a_value_group(self):
        node = lucene_parse('status:(New OR -Closed)')

        self.assertIsInstance(node.children[1], NotNode)
        self.assertEqual('status', node.children[1].child.field)


class TestComparisons(TestCase):

    def test_greater_or_equal(self):
        node = lucene_parse('severity:>=High')

        self.assertIsInstance(node.value, ComparisonValue)
        self.assertEqual('gte', node.value.operator)
        self.assertEqual('High', node.value.text)

    def test_strict_greater(self):
        node = lucene_parse('created:>now-24h')

        self.assertEqual('gt', node.value.operator)
        self.assertEqual('now-24h', node.value.text)

    def test_less_or_equal(self):
        self.assertEqual('lte', lucene_parse('id:<=10').value.operator)

    def test_strict_less(self):
        self.assertEqual('lt', lucene_parse('id:<10').value.operator)

    def test_comparison_without_a_value_is_refused(self):
        with self.assertRaises(SearchQueryError) as caught:
            lucene_parse('id:>')

        # The caret lands where the missing value should have been.
        self.assertEqual(4, caught.exception.get_position())


class TestRanges(TestCase):

    def test_inclusive_range(self):
        node = lucene_parse('created:[2026-01-01 TO 2026-02-01]')

        self.assertIsInstance(node.value, RangeValue)
        self.assertEqual('2026-01-01', node.value.lower)
        self.assertEqual('2026-02-01', node.value.upper)
        self.assertTrue(node.value.include_lower)
        self.assertTrue(node.value.include_upper)

    def test_exclusive_range(self):
        node = lucene_parse('created:{2026-01-01 TO 2026-02-01}')

        self.assertFalse(node.value.include_lower)
        self.assertFalse(node.value.include_upper)

    def test_half_open_range(self):
        node = lucene_parse('created:[2026-01-01 TO *]')

        self.assertEqual('2026-01-01', node.value.lower)
        self.assertIsNone(node.value.upper)

    def test_mixed_bracket_range(self):
        node = lucene_parse('id:[1 TO 10}')

        self.assertTrue(node.value.include_lower)
        self.assertFalse(node.value.include_upper)

    def test_range_without_to_is_refused(self):
        with self.assertRaises(SearchQueryError) as caught:
            lucene_parse('created:[2026-01-01 2026-02-01]')

        self.assertIn('TO', caught.exception.get_message())

    def test_range_with_two_open_bounds_is_refused(self):
        with self.assertRaises(SearchQueryError):
            lucene_parse('created:[* TO *]')

    def test_unterminated_range_is_refused(self):
        with self.assertRaises(SearchQueryError):
            lucene_parse('created:[2026-01-01 TO 2026-02-01')


class TestLexingTrapsFromRealQueries(TestCase):
    """Shapes analysts paste into a triage bar that must not be read as
    field references."""

    def test_url_is_one_term(self):
        node = lucene_parse('https://example.com/alert/1')

        self.assertIsNone(node.field)
        self.assertEqual('https://example.com/alert/1', node.value.text)

    def test_ipv6_address_is_one_term(self):
        node = lucene_parse('fe80::1')

        self.assertIsNone(node.field)
        self.assertEqual('fe80::1', node.value.text)

    def test_filesystem_path_is_one_term(self):
        node = lucene_parse('asset:/var/log/messages')

        self.assertEqual('/var/log/messages', node.value.text)

    def test_trailing_slash_path_is_not_mistaken_for_a_regex(self):
        node = lucene_parse('asset:/var/log/')

        self.assertEqual('/var/log/', node.value.text)

    def test_wildcards_survive_to_the_compiler(self):
        node = lucene_parse('asset:*.corp.local')

        self.assertEqual('*.corp.local', node.value.text)


class TestRefusedFeatures(TestCase):
    """Lucene syntax with no Postgres answer behind it.

    Each of these would otherwise parse as an ordinary term and search
    for its literal text — an answer that looks right and is not.
    """

    def test_fuzzy_is_refused(self):
        with self.assertRaises(SearchQueryError) as caught:
            lucene_parse('title:ransom~2')

        self.assertIn('Fuzzy', caught.exception.get_message())
        self.assertEqual(12, caught.exception.get_position())

    def test_proximity_is_refused(self):
        with self.assertRaises(SearchQueryError) as caught:
            lucene_parse('title:"brute force"~5')

        self.assertIn('Fuzzy', caught.exception.get_message())

    def test_boost_is_refused(self):
        with self.assertRaises(SearchQueryError) as caught:
            lucene_parse('title:ransom^3')

        self.assertIn('boosting', caught.exception.get_message())
        self.assertEqual(12, caught.exception.get_position())

    def test_regex_is_refused(self):
        with self.assertRaises(SearchQueryError) as caught:
            lucene_parse('title:/^ransom.*$/')

        self.assertIn('Regular-expression', caught.exception.get_message())

    def test_escaped_tilde_is_an_ordinary_character(self):
        node = lucene_parse('title:ransom\\~2')

        self.assertEqual('ransom\\~2', node.value.text)


class TestSyntaxErrors(TestCase):

    def test_unterminated_phrase_points_at_the_opening_quote(self):
        with self.assertRaises(SearchQueryError) as caught:
            lucene_parse('title:"unterminated')

        self.assertEqual(6, caught.exception.get_position())

    def test_unbalanced_parenthesis_points_at_the_opening_one(self):
        with self.assertRaises(SearchQueryError) as caught:
            lucene_parse('(a OR b')

        self.assertEqual(0, caught.exception.get_position())

    def test_trailing_operator_is_refused(self):
        with self.assertRaises(SearchQueryError):
            lucene_parse('title:foo AND')

    def test_stray_closing_parenthesis_is_refused(self):
        with self.assertRaises(SearchQueryError) as caught:
            lucene_parse('a b)')

        self.assertEqual(3, caught.exception.get_position())

    def test_field_without_a_value_is_refused(self):
        with self.assertRaises(SearchQueryError):
            lucene_parse('status:')


class TestGuardRails(TestCase):
    """A search bar is a paste target. None of these limits is reachable
    by hand, and all of them stop a pasted blob from becoming a query
    that fans out over the whole alerts table."""

    def test_over_long_expression_is_refused(self):
        with self.assertRaises(SearchQueryError) as caught:
            lucene_parse('a' * (MAX_QUERY_LENGTH + 1))

        self.assertIn('too long', caught.exception.get_message())

    def test_length_limit_is_not_hit_by_a_realistic_query(self):
        expression = 'is:open owner:me severity:>=High -status:Closed'
        self.assertIsNotNone(lucene_parse(expression))

    def test_too_many_terms_is_refused(self):
        with self.assertRaises(SearchQueryError) as caught:
            lucene_parse(' '.join(f't{index}' for index in range(MAX_NODES + 1)))

        self.assertIn('too complex', caught.exception.get_message())

    def test_too_deeply_nested_is_refused(self):
        expression = '(' * (MAX_DEPTH + 2) + 'a' + ')' * (MAX_DEPTH + 2)

        with self.assertRaises(SearchQueryError) as caught:
            lucene_parse(expression)

        self.assertIn('nested too deeply', caught.exception.get_message())


class TestRealisticExpressions(TestCase):
    """The queries the feature exists for, end to end through the parser."""

    def test_triage_queue(self):
        node = lucene_parse('is:open owner:me severity:>=High')

        self.assertIsInstance(node, AndNode)
        self.assertEqual(['is', 'owner', 'severity'], [c.field for c in node.children])

    def test_source_or_source_with_a_tag(self):
        node = lucene_parse('(source:crowdstrike OR source:sentinel) tag:phishing')

        self.assertIsInstance(node, AndNode)
        self.assertIsInstance(node.children[0], OrNode)
        self.assertEqual('tag', node.children[1].field)

    def test_host_and_recency(self):
        node = lucene_parse('asset:*.corp.local created:>now-24h')

        self.assertIsInstance(node, AndNode)
        self.assertIsInstance(node.children[0].value, TermValue)
        self.assertIsInstance(node.children[1].value, ComparisonValue)
