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

"""Tokenizer and recursive-descent parser for the alert search bar.

The grammar is a deliberate subset of Lucene — everything it accepts can
be answered by a plain Postgres `WHERE` clause. Anything outside it is a
hard error carrying a character offset, never a silently-ignored token:
a query that quietly means something other than what was typed is worse
in a triage queue than one that refuses to run.

    query      := or_expr
    or_expr    := and_expr ( ("OR" | "||") and_expr )*
    and_expr   := unary ( ("AND" | "&&")? unary )*      # juxtaposition = AND
    unary      := ("NOT" | "!" | "-" | "+")? primary
    primary    := "(" or_expr ")" | clause
    clause     := [ field ":" ] value
    value      := "(" value_group ")" | range | comparison | phrase | term
    range      := ("[" | "{") bound "TO" bound ("]" | "}")
    comparison := (">=" | "<=" | ">" | "<") term
    phrase     := '"' chars '"'
    term       := unquoted run; `*` and `?` are wildcards, `\\` escapes

Two places where the default operator differs, on purpose:

  * At the top level, juxtaposition means AND — `crowdstrike phishing`
    narrows, which is what a search bar is for.
  * Inside a value group, juxtaposition means OR — `status:(New Open)`
    can only sensibly mean "either", since one alert holds one status.
    Kibana behaves the same way and analysts arrive expecting it.

This module holds no SQLAlchemy import and touches no database: it turns
text into an AST and nothing else, which is what makes it testable
without a stack. Resolving field names and values is `query_compiler`'s
job.
"""

import re
from dataclasses import dataclass
from typing import Optional
from typing import Union

from app.models.errors import SearchQueryError

# Guard rails. A search bar is a paste target, and every one of these
# limits exists to stop a pasted blob from becoming a query that fans
# out over the whole alerts table. They are generous next to anything a
# human types by hand.
MAX_QUERY_LENGTH = 4096
MAX_NODES = 200
MAX_DEPTH = 16

_KIND_FIELD = 'field'
_KIND_TERM = 'term'
_KIND_PHRASE = 'phrase'
_KIND_LPAREN = 'lparen'
_KIND_RPAREN = 'rparen'
_KIND_LBRACKET = 'lbracket'
_KIND_RBRACKET = 'rbracket'
_KIND_LBRACE = 'lbrace'
_KIND_RBRACE = 'rbrace'
_KIND_AND = 'and'
_KIND_OR = 'or'
_KIND_NOT = 'not'
_KIND_REQUIRE = 'require'
_KIND_TO = 'to'
_KIND_EOF = 'eof'

#: Characters that end an unquoted term run. `:` is deliberately absent —
#: it is only special when it follows something shaped like a field name,
#: which `_FIELD_RE` decides, so IOC values like `fe80::1` survive.
_TERM_STOP = set(' \t\r\n()[]{}"')

#: A field name followed by its colon. Dots are allowed so JSON paths
#: (`context.rule_name`) and relationship paths (`assets.asset_name`)
#: lex as one field.
_FIELD_RE = re.compile(r'[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_*]+)*:')

#: Bare words that are operators rather than terms. Uppercase only, per
#: Lucene: `to`, `and` and `or` are ordinary words an analyst may well be
#: searching for.
_OPERATOR_WORDS = {
    'AND': _KIND_AND,
    'OR': _KIND_OR,
    'NOT': _KIND_NOT,
    'TO': _KIND_TO,
}

#: Lucene features with no Postgres answer behind them. Accepting these
#: and ignoring the modifier would return results that look right and
#: are not, so they are rejected by name.
_UNSUPPORTED = (
    ('~', 'Fuzzy and proximity search (~) is not supported'),
    ('^', 'Term boosting (^) is not supported'),
)

#: Metacharacters that mark a `/…/` run as a regular expression rather
#: than a filesystem path. `.` and `*` are deliberately absent: paths and
#: hostnames are full of them, and rejecting `asset:/var/log/*.gz` to
#: catch a regex nobody wrote is the worse trade.
_REGEX_MARKERS = set('[]()|+?^$')

_COMPARISON_PREFIXES = (
    ('>=', 'gte'),
    ('<=', 'lte'),
    ('>', 'gt'),
    ('<', 'lt'),
)


@dataclass(frozen=True)
class TermValue:
    """A bare word or a quoted phrase.

    `text` is the *raw* source text with escape sequences intact, so the
    compiler can still tell a wildcard `*` from an escaped `\\*` when it
    builds the LIKE pattern. `quoted` suppresses wildcards entirely.
    """

    text: str
    quoted: bool
    position: int


@dataclass(frozen=True)
class RangeValue:
    """`[a TO b]` (inclusive) or `{a TO b}` (exclusive). `None` = open."""

    lower: Optional[str]
    upper: Optional[str]
    include_lower: bool
    include_upper: bool
    position: int


@dataclass(frozen=True)
class ComparisonValue:
    """`>=x` / `<=x` / `>x` / `<x`. `operator` is a `build_condition` name."""

    operator: str
    text: str
    position: int


@dataclass(frozen=True)
class ClauseNode:
    """One `field:value` pair. `field` is `None` for a free-text term."""

    field: Optional[str]
    value: Union[TermValue, RangeValue, ComparisonValue]
    position: int


@dataclass(frozen=True)
class AndNode:
    children: tuple


@dataclass(frozen=True)
class OrNode:
    children: tuple


@dataclass(frozen=True)
class NotNode:
    child: object


@dataclass(frozen=True)
class _Token:
    kind: str
    text: str
    position: int


def _unsupported_check(raw: str, offset: int) -> None:
    """Reject a term carrying a modifier we cannot honour.

    Scans for unescaped `~` / `^` and points at the offending character,
    then checks the whole run for regex delimiters. Half-honouring any of
    these — searching for the literal text of a regex, say — returns
    results that look right and are not.
    """
    # Shape first: `/^foo$/` holds a `^`, and reporting that as a boost
    # would send the user looking for the wrong mistake.
    if len(raw) > 2 and raw.startswith('/') and raw.endswith('/'):
        if any(char in _REGEX_MARKERS for char in raw):
            raise SearchQueryError(
                'Regular-expression search (/…/) is not supported — quote the '
                'value to search for it literally',
                offset,
            )

    index = 0
    while index < len(raw):
        char = raw[index]
        if char == '\\':
            index += 2
            continue
        for marker, message in _UNSUPPORTED:
            if char == marker:
                raise SearchQueryError(message, offset + index)
        index += 1


class _Lexer:
    """Source text to a flat token list.

    Kept separate from the parser so the same tokens can drive a syntax
    highlighter later without re-deriving the escaping rules.
    """

    def __init__(self, source: str):
        self._source = source
        self._index = 0
        self._length = len(source)

    def _peek(self, ahead: int = 0) -> str:
        position = self._index + ahead
        if position >= self._length:
            return ''
        return self._source[position]

    def _read_phrase(self) -> _Token:
        start = self._index
        self._index += 1  # opening quote
        chunk = []
        while self._index < self._length:
            char = self._source[self._index]
            if char == '\\':
                # Keep the escape intact: the compiler needs to know the
                # next character was escaped, not just what it was.
                chunk.append(char)
                self._index += 1
                if self._index < self._length:
                    chunk.append(self._source[self._index])
                    self._index += 1
                continue
            if char == '"':
                self._index += 1
                return _Token(_KIND_PHRASE, ''.join(chunk), start)
            chunk.append(char)
            self._index += 1

        raise SearchQueryError('Unterminated quoted phrase', start)

    def _read_term(self) -> _Token:
        start = self._index
        chunk = []
        while self._index < self._length:
            char = self._source[self._index]
            if char == '\\':
                chunk.append(char)
                self._index += 1
                if self._index < self._length:
                    chunk.append(self._source[self._index])
                    self._index += 1
                continue
            if char in _TERM_STOP:
                break
            chunk.append(char)
            self._index += 1

        raw = ''.join(chunk)
        _unsupported_check(raw, start)

        operator_kind = _OPERATOR_WORDS.get(raw)
        if operator_kind is not None:
            return _Token(operator_kind, raw, start)

        return _Token(_KIND_TERM, raw, start)

    def _try_read_field(self) -> Optional[_Token]:
        """Emit a FIELD token when the run ahead looks like `name:`.

        Two shapes have to escape this, because both are things analysts
        paste into a triage search and neither is a field reference:

          * a URL — `http://host/path` would lex as the field `http`, so
            a `:` followed by `//` stays part of the term;
          * an IPv6 address — `fe80::1` would lex as the field `fe80`, so
            a doubled `::` stays part of the term too.

        A single-colon IPv6 form (`fe80:aaaa:…`) is still read as a field
        and fails later with an "unknown field" error naming `fe80`. That
        is the honest outcome: quoting it (`"fe80:aaaa::1"`) searches for
        it literally.
        """
        match = _FIELD_RE.match(self._source, self._index)
        if match is None:
            return None
        if self._source[match.end():match.end() + 2] == '//':
            return None
        if self._source[match.end():match.end() + 1] == ':':
            return None

        token = _Token(_KIND_FIELD, match.group()[:-1], self._index)
        self._index = match.end()
        return token

    def tokenize(self) -> list:
        tokens = []
        singles = {
            '(': _KIND_LPAREN,
            ')': _KIND_RPAREN,
            '[': _KIND_LBRACKET,
            ']': _KIND_RBRACKET,
            '{': _KIND_LBRACE,
            '}': _KIND_RBRACE,
        }

        while self._index < self._length:
            char = self._source[self._index]

            if char.isspace():
                self._index += 1
                continue

            single = singles.get(char)
            if single is not None:
                tokens.append(_Token(single, char, self._index))
                self._index += 1
                continue

            if char == '"':
                tokens.append(self._read_phrase())
                continue

            if char == '&' and self._peek(1) == '&':
                tokens.append(_Token(_KIND_AND, '&&', self._index))
                self._index += 2
                continue

            if char == '|' and self._peek(1) == '|':
                tokens.append(_Token(_KIND_OR, '||', self._index))
                self._index += 2
                continue

            # `!` / `-` / `+` are prefix operators only where a clause can
            # begin. Mid-word they are ordinary characters, which is what
            # keeps `HOST-1` and `a+b` from splitting into three tokens.
            if char in '!-+' and self._at_clause_boundary(tokens):
                kind = _KIND_REQUIRE if char == '+' else _KIND_NOT
                tokens.append(_Token(kind, char, self._index))
                self._index += 1
                continue

            field = self._try_read_field()
            if field is not None:
                tokens.append(field)
                continue

            tokens.append(self._read_term())

        tokens.append(_Token(_KIND_EOF, '', self._length))
        return tokens

    def _at_clause_boundary(self, tokens: list) -> bool:
        """True when a new clause may start at the current index.

        `-` opens a negation only at the very start, after an operator,
        or after an opening bracket — the positions where a value cannot
        already be in progress. Notably *not* straight after `field:`:
        there `-` belongs to the value, so `case:-1` and `title:-foo`
        mean what they look like. Negating a whole clause is `-field:x`
        or `NOT field:x`.
        """
        if not tokens:
            return True
        previous = tokens[-1].kind
        return previous in (
            _KIND_LPAREN,
            _KIND_LBRACKET,
            _KIND_LBRACE,
            _KIND_AND,
            _KIND_OR,
            _KIND_NOT,
            _KIND_REQUIRE,
        )


class _Parser:

    def __init__(self, tokens: list):
        self._tokens = tokens
        self._index = 0
        self._nodes = 0

    def _current(self) -> _Token:
        return self._tokens[self._index]

    def _advance(self) -> _Token:
        token = self._tokens[self._index]
        self._index += 1
        return token

    def _count_node(self, position: int) -> None:
        self._nodes += 1
        if self._nodes > MAX_NODES:
            raise SearchQueryError(
                f'Search expression is too complex (over {MAX_NODES} terms)', position
            )

    def parse(self):
        node = self._parse_or(depth=0)
        current = self._current()
        if current.kind != _KIND_EOF:
            raise SearchQueryError(f"Unexpected '{current.text}'", current.position)
        return node

    def _parse_or(self, depth: int):
        if depth > MAX_DEPTH:
            raise SearchQueryError(
                f'Search expression is nested too deeply (over {MAX_DEPTH} levels)',
                self._current().position,
            )

        children = [self._parse_and(depth)]
        while self._current().kind == _KIND_OR:
            self._advance()
            children.append(self._parse_and(depth))

        if len(children) == 1:
            return children[0]
        return OrNode(tuple(children))

    def _parse_and(self, depth: int):
        children = [self._parse_unary(depth)]

        while True:
            current = self._current()
            if current.kind == _KIND_AND:
                self._advance()
                children.append(self._parse_unary(depth))
                continue
            if _can_start_clause(current.kind):
                # Juxtaposition — `a b` means `a AND b`.
                children.append(self._parse_unary(depth))
                continue
            break

        if len(children) == 1:
            return children[0]
        return AndNode(tuple(children))

    def _parse_unary(self, depth: int):
        current = self._current()

        if current.kind == _KIND_NOT:
            self._advance()
            return NotNode(self._parse_unary(depth))

        if current.kind == _KIND_REQUIRE:
            # `+a` is "must match", which is already what AND-by-default
            # gives us. Consume it so the query still parses rather than
            # pretending the syntax does something extra.
            self._advance()
            return self._parse_unary(depth)

        return self._parse_primary(depth)

    def _parse_primary(self, depth: int):
        current = self._current()

        if current.kind == _KIND_LPAREN:
            self._advance()
            node = self._parse_or(depth + 1)
            closing = self._current()
            if closing.kind != _KIND_RPAREN:
                raise SearchQueryError('Unbalanced parenthesis', current.position)
            self._advance()
            return node

        if current.kind == _KIND_FIELD:
            self._advance()
            return self._parse_value(current.text, current.position, depth)

        return self._parse_value(None, current.position, depth)

    def _parse_value(self, field: Optional[str], position: int, depth: int):
        current = self._current()

        # A field-less `(` is an ordinary group and never reaches here —
        # `_parse_primary` consumes it before calling us.
        if current.kind == _KIND_LPAREN and field is not None:
            return self._parse_value_group(field, depth + 1)

        if current.kind in (_KIND_LBRACKET, _KIND_LBRACE):
            return self._parse_range(field)

        if current.kind == _KIND_PHRASE:
            self._advance()
            self._count_node(current.position)
            return ClauseNode(field, TermValue(current.text, True, current.position), position)

        if current.kind == _KIND_TERM:
            self._advance()
            self._count_node(current.position)
            return ClauseNode(field, _term_value(current), position)

        if current.kind == _KIND_EOF:
            raise SearchQueryError('Search expression ends unexpectedly', current.position)

        raise SearchQueryError(f"Unexpected '{current.text}'", current.position)

    def _parse_value_group(self, field: str, depth: int):
        """`field:(a OR b)` — juxtaposition inside means OR, see module doc."""
        if depth > MAX_DEPTH:
            raise SearchQueryError(
                f'Search expression is nested too deeply (over {MAX_DEPTH} levels)',
                self._current().position,
            )

        opening = self._advance()  # '('
        node = self._parse_group_or(field, depth)

        closing = self._current()
        if closing.kind != _KIND_RPAREN:
            raise SearchQueryError('Unbalanced parenthesis', opening.position)
        self._advance()
        return node

    def _parse_group_or(self, field: str, depth: int):
        children = [self._parse_group_and(field, depth)]
        while self._current().kind == _KIND_OR:
            self._advance()
            children.append(self._parse_group_and(field, depth))

        if len(children) == 1:
            return children[0]
        return OrNode(tuple(children))

    def _parse_group_and(self, field: str, depth: int):
        children = [self._parse_group_unary(field, depth)]
        explicit_and = False

        while True:
            current = self._current()
            if current.kind == _KIND_AND:
                self._advance()
                explicit_and = True
                children.append(self._parse_group_unary(field, depth))
                continue
            if _can_start_clause(current.kind):
                children.append(self._parse_group_unary(field, depth))
                continue
            break

        if len(children) == 1:
            return children[0]
        # Only an explicit AND means AND here; bare juxtaposition is OR.
        if explicit_and:
            return AndNode(tuple(children))
        return OrNode(tuple(children))

    def _parse_group_unary(self, field: str, depth: int):
        current = self._current()

        if current.kind == _KIND_NOT:
            self._advance()
            return NotNode(self._parse_group_unary(field, depth))

        if current.kind == _KIND_REQUIRE:
            self._advance()
            return self._parse_group_unary(field, depth)

        if current.kind == _KIND_LPAREN:
            return self._parse_value_group(field, depth + 1)

        return self._parse_value(field, current.position, depth)

    def _parse_range(self, field: Optional[str]):
        opening = self._advance()
        include_lower = opening.kind == _KIND_LBRACKET

        lower = self._parse_bound(opening)

        separator = self._current()
        if separator.kind != _KIND_TO:
            raise SearchQueryError("Range needs a 'TO' between its bounds", separator.position)
        self._advance()

        upper = self._parse_bound(opening)

        closing = self._current()
        if closing.kind not in (_KIND_RBRACKET, _KIND_RBRACE):
            raise SearchQueryError('Unterminated range', opening.position)
        self._advance()
        include_upper = closing.kind == _KIND_RBRACKET

        if lower is None and upper is None:
            raise SearchQueryError('A range needs at least one bound', opening.position)

        self._count_node(opening.position)
        return ClauseNode(
            field,
            RangeValue(lower, upper, include_lower, include_upper, opening.position),
            opening.position,
        )

    def _parse_bound(self, opening: _Token) -> Optional[str]:
        """One end of a range. `*` is the open bound, as in Lucene."""
        current = self._current()
        if current.kind not in (_KIND_TERM, _KIND_PHRASE):
            raise SearchQueryError('Range bound is missing', opening.position)
        self._advance()
        if current.kind == _KIND_TERM and current.text == '*':
            return None
        return current.text


def _term_value(token: _Token):
    """A TERM token as either a comparison or a plain term.

    `created:>now-7d` lexes as one term because `>` is not a stop
    character; splitting it here keeps the lexer free of parser state.
    """
    for prefix, operator in _COMPARISON_PREFIXES:
        if token.text.startswith(prefix):
            rest = token.text[len(prefix):]
            if rest == '':
                raise SearchQueryError(
                    f"'{prefix}' needs a value after it", token.position + len(prefix)
                )
            return ComparisonValue(operator, rest, token.position)

    return TermValue(token.text, False, token.position)


def _can_start_clause(kind: str) -> bool:
    return kind in (
        _KIND_TERM,
        _KIND_PHRASE,
        _KIND_FIELD,
        _KIND_LPAREN,
        _KIND_LBRACKET,
        _KIND_LBRACE,
        _KIND_NOT,
        _KIND_REQUIRE,
    )


def lucene_parse(source: str):
    """Parse a search expression into an AST.

    Returns `None` for an empty or whitespace-only expression — callers
    treat that as "no query", not as an error. Raises `SearchQueryError`
    with a character offset for anything malformed.
    """
    if source is None:
        return None

    if len(source) > MAX_QUERY_LENGTH:
        raise SearchQueryError(
            f'Search expression is too long (over {MAX_QUERY_LENGTH} characters)'
        )

    if source.strip() == '':
        return None

    tokens = _Lexer(source).tokenize()
    return _Parser(tokens).parse()
