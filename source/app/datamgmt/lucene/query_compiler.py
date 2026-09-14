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

"""Turns a parsed search expression into one SQLAlchemy clause.

The clause this produces is a *single conjunct*. Callers append it to
the condition list they already build and combine with `and_`, so the
tenancy predicate — `alert_customer_id IN (…)` — stays outside it and no
`OR` an analyst writes can widen what they see. That is the security
property of this module, and it is a property of how the result is used,
so do not hand the clause to anything that combines it with `or_`.

Two further rules worth stating, because both are easy to get wrong and
neither shows up in a smoke test:

  * **Relationship predicates compile to `.any()` (EXISTS), never to a
    join.** Under a join, `asset:HOST-1 OR title:foo` silently drops
    every alert that has no assets at all, because the join eliminated
    the row before the OR was evaluated.
  * **Negation is `IS NOT TRUE`, not `NOT`.** Postgres three-valued
    logic makes `NOT (alert_owner_id = 5)` false for an unowned alert,
    so `-owner:jdoe` would hide exactly the unassigned alerts an analyst
    is usually hunting for.

Values are resolved against the database — `status:Closed` becomes a
status id — which is why this lives in `datamgmt` and not next to the
parser.
"""

import re
import uuid as uuid_module
from datetime import datetime
from datetime import timedelta
from difflib import get_close_matches

from sqlalchemy import and_
from sqlalchemy import false
from sqlalchemy import func
from sqlalchemy import or_
from sqlalchemy import true

from app.datamgmt.filtering import build_condition
from app.datamgmt.filtering import build_json_condition
from app.datamgmt.filtering import LIKE_ESCAPE_CHARACTER
from app.datamgmt.lucene.alert_fields import alert_field_for_alias
from app.datamgmt.lucene.alert_fields import alert_field_raw_column
from app.datamgmt.lucene.alert_fields import DEFAULT_SEARCH_COLUMNS
from app.datamgmt.lucene.alert_fields import IS_MACROS
from app.datamgmt.lucene.alert_fields import KIND_DATE
from app.datamgmt.lucene.alert_fields import KIND_INTEGER
from app.datamgmt.lucene.alert_fields import KIND_JSON
from app.datamgmt.lucene.alert_fields import KIND_MACRO
from app.datamgmt.lucene.alert_fields import KIND_MEMBERSHIP
from app.datamgmt.lucene.alert_fields import KIND_RELATION
from app.datamgmt.lucene.alert_fields import KIND_TEXT
from app.datamgmt.lucene.alert_fields import KIND_USER
from app.datamgmt.lucene.alert_fields import KIND_UUID
from app.datamgmt.lucene.alert_fields import OWNER_SELF
from app.datamgmt.lucene.alert_fields import suggest_alert_field
from app.datamgmt.lucene.alert_fields import TERMINAL_ALERT_STATUS_NAMES
from app.datamgmt.lucene.alert_fields import VALUE_NONE
from app.datamgmt.lucene.query_parser import AndNode
from app.datamgmt.lucene.query_parser import ClauseNode
from app.datamgmt.lucene.query_parser import ComparisonValue
from app.datamgmt.lucene.query_parser import lucene_parse
from app.datamgmt.lucene.query_parser import NotNode
from app.datamgmt.lucene.query_parser import OrNode
from app.datamgmt.lucene.query_parser import RangeValue
from app.datamgmt.lucene.query_parser import TermValue
from app.db import db
from app.iris_engine.utils.common import parse_bf_date_format
from app.models.alerts import Alert
from app.models.alerts import AlertStatus
from app.models.errors import SearchQueryError

#: `now`, `now-24h`, `now+1d`. Anchored to *now* rather than to a stored
#: column so a saved filter stays a rolling window.
_RELATIVE_DATE_RE = re.compile(r'^now(?:([+-])(\d+)([smhdw]))?$', re.IGNORECASE)

_RELATIVE_UNITS = {
    's': 1,
    'm': 60,
    'h': 3600,
    'd': 86400,
    'w': 604800,
}

_DATE_ONLY_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')

#: Comparison operators, in the spelling `build_condition` expects.
_LOWER_BOUND_OPERATORS = ('gt', 'gte')


def _unescape(raw: str) -> str:
    """Drop the parser's escape characters, for a value compared as-is."""
    chunk = []
    index = 0
    while index < len(raw):
        if raw[index] == '\\' and index + 1 < len(raw):
            chunk.append(raw[index + 1])
            index += 2
            continue
        chunk.append(raw[index])
        index += 1
    return ''.join(chunk)


def _escape_like_character(char: str) -> str:
    if char in ('%', '_', LIKE_ESCAPE_CHARACTER):
        return LIKE_ESCAPE_CHARACTER + char
    return char


def _like_pattern(value: TermValue):
    """`(pattern, has_wildcard)` for a term.

    `*` and `?` become `%` and `_`; a `%` or `_` the analyst actually
    typed is escaped so it stays literal, which is the whole reason this
    cannot be done by string-formatting at the call site. A quoted
    phrase has no wildcards at all — quoting is how you search for a
    literal asterisk.
    """
    chunk = []
    has_wildcard = False
    index = 0
    raw = value.text
    while index < len(raw):
        char = raw[index]
        if char == '\\' and index + 1 < len(raw):
            chunk.append(_escape_like_character(raw[index + 1]))
            index += 2
            continue
        if not value.quoted and char == '*':
            chunk.append('%')
            has_wildcard = True
        elif not value.quoted and char == '?':
            chunk.append('_')
            has_wildcard = True
        else:
            chunk.append(_escape_like_character(char))
        index += 1

    return ''.join(chunk), has_wildcard


def _substring_pattern(value: TermValue) -> str:
    """The pattern a text clause matches on.

    Without a wildcard the term is a substring — that is what a search
    bar means by `title:ransom`. With one, the pattern is anchored, so
    `asset:*.corp.local` matches hosts *ending* in that domain rather
    than anything containing it.
    """
    pattern, has_wildcard = _like_pattern(value)
    if has_wildcard:
        return pattern
    return f'%{pattern}%'


def _relative_datetime(text: str):
    """`now±Nunit` as a datetime, or `None` if it is not that shape."""
    match = _RELATIVE_DATE_RE.match(text)
    if match is None:
        return None

    reference = datetime.utcnow()
    sign, amount, unit = match.groups()
    if sign is None:
        return reference

    seconds = int(amount) * _RELATIVE_UNITS[unit.lower()]
    if sign == '-':
        return reference - timedelta(seconds=seconds)
    return reference + timedelta(seconds=seconds)


def _parse_datetime(text: str, position: int, end_of_day: bool = False):
    """A date bound, with the "bare date means the whole day" rule.

    `created:2026-07-15` has to cover the fifteenth, not the instant it
    began — the same correction `_parse_inclusive_date_range` applies to
    the grid's date filters, kept identical here so the two surfaces
    answer a date the same way.
    """
    relative = _relative_datetime(text)
    if relative is not None:
        return relative

    parsed = parse_bf_date_format(text)
    if not parsed:
        raise SearchQueryError(
            f"'{text}' is not a date — use 2026-07-15, 2026-07-15T09:00 or now-24h",
            position,
        )

    if end_of_day and _DATE_ONLY_RE.match(text.strip()):
        return parsed.replace(hour=23, minute=59, second=59, microsecond=999999)
    return parsed


class AlertQueryCompiler:
    """Compiles one expression. One instance per request — it caches the
    name→id lookups it resolves, and those are only valid for as long as
    the surrounding transaction is."""

    def __init__(self, user_identifier=None):
        self._user_identifier = user_identifier
        self._lookup_cache = {}
        self._status_cache = None

    def compile(self, node):
        return self._compile_node(node)

    # -- tree ------------------------------------------------------------

    def _compile_node(self, node):
        if isinstance(node, AndNode):
            return and_(*[self._compile_node(child) for child in node.children])

        if isinstance(node, OrNode):
            return or_(*[self._compile_node(child) for child in node.children])

        if isinstance(node, NotNode):
            # `IS NOT TRUE`, not `NOT` — see the module docstring. Under
            # plain `NOT`, an unowned alert fails `-owner:jdoe` because
            # `NULL = 5` is NULL, and the analyst loses the rows they
            # were most likely looking for.
            return self._compile_node(node.child).is_not(True)

        if isinstance(node, ClauseNode):
            return self._compile_clause(node)

        raise SearchQueryError('Search expression could not be understood')

    # -- clauses ---------------------------------------------------------

    def _compile_clause(self, node: ClauseNode):
        if node.field is None:
            return self._compile_free_text(node)

        descriptor = alert_field_for_alias(node.field)
        if descriptor is None:
            return self._compile_raw_column(node)

        if descriptor.kind == KIND_MACRO:
            return self._compile_macro(node)

        if descriptor.kind == KIND_JSON:
            return self._compile_json(descriptor, node)

        if descriptor.kind == KIND_RELATION:
            return self._compile_relation(descriptor, node)

        if descriptor.kind == KIND_MEMBERSHIP:
            return self._compile_membership(descriptor, node)

        column = getattr(Alert, descriptor.column)

        if descriptor.kind == KIND_TEXT:
            return self._compile_text(descriptor, column, node)

        if descriptor.kind == KIND_INTEGER:
            return self._compile_integer(descriptor, column, node)

        if descriptor.kind == KIND_UUID:
            return self._compile_uuid(descriptor, column, node)

        if descriptor.kind == KIND_DATE:
            return self._compile_date(descriptor, column, node)

        if descriptor.kind == KIND_USER:
            return self._compile_user(descriptor, column, node)

        # Everything left is a name-to-id lookup: status, severity,
        # resolution, classification, customer.
        return self._compile_enum(descriptor, column, node)

    def _compile_free_text(self, node: ClauseNode):
        value = node.value
        if not isinstance(value, TermValue):
            raise SearchQueryError(
                'A range or comparison needs a field, for example created:[2026-01-01 TO now]',
                value.position,
            )

        pattern = _substring_pattern(value)
        return or_(*[
            build_condition(getattr(Alert, name), 'ilike_pattern', pattern)
            for name in DEFAULT_SEARCH_COLUMNS
        ])

    def _compile_raw_column(self, node: ClauseNode):
        """The escape hatch: a literal `Alert` column name.

        Anything the filter grid can reach stays reachable from the bar
        even before it earns a friendly alias, so nobody is forced back
        into hand-written `custom_conditions`.
        """
        column = alert_field_raw_column(node.field)
        if column is None:
            suggestion = suggest_alert_field(node.field)
            hint = f" — did you mean '{suggestion}'?" if suggestion else ''
            raise SearchQueryError(f"Unknown field '{node.field}'{hint}", node.position)

        value = node.value
        if isinstance(value, TermValue):
            return build_condition(column, 'ilike_pattern', _substring_pattern(value))
        if isinstance(value, ComparisonValue):
            return build_condition(column, value.operator, _unescape(value.text))
        return self._bounded(column, value, lambda text, _position, _end: _unescape(text))

    def _compile_text(self, descriptor, column, node: ClauseNode):
        value = node.value
        if not isinstance(value, TermValue):
            raise SearchQueryError(
                f"'{descriptor.alias}' holds text and cannot be compared with "
                f'> or a range — use {descriptor.alias}:value',
                value.position,
            )
        return build_condition(column, 'ilike_pattern', _substring_pattern(value))

    def _compile_integer(self, descriptor, column, node: ClauseNode):
        value = node.value

        if isinstance(value, TermValue):
            pattern, has_wildcard = _like_pattern(value)
            if has_wildcard:
                return build_condition(column, 'ilike_pattern', pattern)
            return build_condition(column, 'eq', self._as_integer(descriptor, value))

        if isinstance(value, ComparisonValue):
            return build_condition(
                column, value.operator, self._as_integer(descriptor, value)
            )

        return self._bounded(
            column, value, lambda text, position, _end: self._integer_text(
                descriptor, _unescape(text), position
            )
        )

    def _compile_uuid(self, descriptor, column, node: ClauseNode):
        value = node.value
        if not isinstance(value, TermValue):
            raise SearchQueryError(
                f"'{descriptor.alias}' holds a UUID and cannot be compared with "
                '> or a range',
                value.position,
            )

        pattern, has_wildcard = _like_pattern(value)
        if has_wildcard:
            return build_condition(column, 'ilike_pattern', pattern)

        text = _unescape(value.text)
        try:
            uuid_module.UUID(text)
        except ValueError:
            raise SearchQueryError(f"'{text}' is not a UUID", value.position) from None
        return build_condition(column, 'eq', text)

    def _compile_date(self, descriptor, column, node: ClauseNode):
        del descriptor
        value = node.value

        if isinstance(value, TermValue):
            text = _unescape(value.text)
            # A bare date is the whole day, so it reads the way an
            # analyst says it: "created:2026-07-15" means that Tuesday.
            # Anything with a time in it is an instant and compares as
            # one — including `now`, which is why the day-window branch
            # is keyed off the date-only shape rather than off comparing
            # two calls to `_parse_datetime` (two calls to `utcnow()` are
            # microseconds apart, and the window between them is empty).
            if _DATE_ONLY_RE.match(text.strip()):
                return and_(
                    build_condition(column, 'gte', _parse_datetime(text, value.position)),
                    build_condition(
                        column, 'lte',
                        _parse_datetime(text, value.position, end_of_day=True)
                    ),
                )
            return build_condition(column, 'eq', _parse_datetime(text, value.position))

        if isinstance(value, ComparisonValue):
            text = _unescape(value.text)
            # An upper bound on a bare date includes that day; a lower
            # bound starts at its first instant.
            end_of_day = value.operator not in _LOWER_BOUND_OPERATORS
            return build_condition(
                column, value.operator, _parse_datetime(text, value.position, end_of_day)
            )

        return self._bounded(
            column, value,
            lambda text, position, end: _parse_datetime(_unescape(text), position, end)
        )

    def _compile_enum(self, descriptor, column, node: ClauseNode):
        value = node.value

        if isinstance(value, TermValue):
            text = _unescape(value.text)
            if text.lower() == VALUE_NONE:
                return column.is_(None)
            identifiers = self._resolve_lookup(descriptor, value)
            if len(identifiers) == 1:
                return build_condition(column, 'eq', identifiers[0])
            return build_condition(column, 'in', identifiers)

        self._require_ordered(descriptor, value)

        if isinstance(value, ComparisonValue):
            # The lookup resolves *terms*; a comparison carries the same
            # text with an operator in front of it, so hand the term over
            # and keep the operator here.
            bound = self._single_lookup(
                descriptor, TermValue(value.text, False, value.position)
            )
            return build_condition(column, value.operator, bound)

        return self._bounded(
            column, value,
            lambda text, position, _end: self._single_lookup(
                descriptor, TermValue(text, False, position)
            )
        )

    def _compile_user(self, descriptor, column, node: ClauseNode):
        value = node.value
        if not isinstance(value, TermValue):
            raise SearchQueryError(
                f"'{descriptor.alias}' names a user and cannot be compared with "
                '> or a range',
                value.position,
            )

        text = _unescape(value.text).lower()

        if text == VALUE_NONE:
            return column.is_(None)

        if text == OWNER_SELF:
            # `owner:me` follows whoever runs the query, which is what
            # makes it worth saving in a shared filter.
            if self._user_identifier is None:
                raise SearchQueryError(
                    f"'{descriptor.alias}:me' needs an authenticated user", value.position
                )
            return build_condition(column, 'eq', self._user_identifier)

        identifiers = self._resolve_lookup(descriptor, value)
        if len(identifiers) == 1:
            return build_condition(column, 'eq', identifiers[0])
        return build_condition(column, 'in', identifiers)

    def _compile_membership(self, descriptor, node: ClauseNode):
        value = node.value
        relation = getattr(Alert, descriptor.relation)
        model, column_name = descriptor.relation_columns[0]

        if not isinstance(value, TermValue):
            raise SearchQueryError(
                f"'{descriptor.alias}' takes an identifier or 'none'", value.position
            )

        text = _unescape(value.text)
        if text.lower() == VALUE_NONE:
            return ~relation.any()

        return relation.any(
            getattr(model, column_name) == self._integer_text(
                descriptor, text, value.position
            )
        )

    def _compile_relation(self, descriptor, node: ClauseNode):
        """EXISTS over a related table — never a join. See the module doc."""
        value = node.value
        if not isinstance(value, TermValue):
            raise SearchQueryError(
                f"'{descriptor.alias}' holds text and cannot be compared with "
                '> or a range',
                value.position,
            )

        pattern = _substring_pattern(value)
        relation = getattr(Alert, descriptor.relation)
        matches = [
            build_condition(getattr(model, column_name), 'ilike_pattern', pattern)
            for model, column_name in descriptor.relation_columns
        ]
        return relation.any(or_(*matches) if len(matches) > 1 else matches[0])

    def _compile_json(self, descriptor, node: ClauseNode):
        path = node.field.split('.', 1)
        if len(path) < 2 or path[1] == '':
            raise SearchQueryError(
                f"'{descriptor.alias}' needs a path, for example "
                f'{descriptor.alias}.rule_name:value',
                node.position,
            )

        column = getattr(Alert, descriptor.column)
        value = node.value

        if isinstance(value, TermValue):
            return build_json_condition(
                column, path[1], 'ilike_pattern', _substring_pattern(value)
            )

        if isinstance(value, ComparisonValue):
            return build_json_condition(
                column, path[1], value.operator, _unescape(value.text)
            )

        return self._bounded(
            column, value,
            lambda text, _position, _end: _unescape(text),
            json_path=path[1],
        )

    def _compile_macro(self, node: ClauseNode):
        value = node.value
        if not isinstance(value, TermValue):
            raise SearchQueryError("'is' takes a single word, like is:open", value.position)

        name = _unescape(value.text).lower()
        if name not in IS_MACROS:
            suggestion = get_close_matches(name, list(IS_MACROS), n=1, cutoff=0.6)
            hint = f" — did you mean 'is:{suggestion[0]}'?" if suggestion else ''
            raise SearchQueryError(f"Unknown state 'is:{name}'{hint}", value.position)

        if name == 'open':
            terminal = self._terminal_status_ids()
            if not terminal:
                return true()
            return Alert.alert_status_id.not_in(terminal)

        if name == 'closed':
            terminal = self._terminal_status_ids()
            if not terminal:
                return false()
            return Alert.alert_status_id.in_(terminal)

        if name == 'assigned':
            return Alert.alert_owner_id.is_not(None)

        if name == 'unassigned':
            return Alert.alert_owner_id.is_(None)

        if name in ('escalated', 'merged'):
            identifiers = self._status_ids_named((name,))
            if not identifiers:
                return false()
            return Alert.alert_status_id.in_(identifiers)

        if name == 'resolved':
            return Alert.alert_resolution_status_id.is_not(None)

        if name == 'unresolved':
            return Alert.alert_resolution_status_id.is_(None)

        if name == 'clustered':
            return Alert.clusters.any()

        if name == 'orphan':
            return ~Alert.clusters.any()

        if name == 'in_case':
            return Alert.cases.any()

        return ~Alert.cases.any()

    # -- helpers ---------------------------------------------------------

    def _bounded(self, column, value: RangeValue, convert, json_path=None):
        """A range as one or two comparisons.

        `convert` turns a bound's text into whatever the column compares
        against, and is told whether it is the upper bound so a bare date
        can roll to the end of its day. Bounds arrive *escaped*, as the
        analyst wrote them, because the enum converter wants to hand the
        text to the same lookup a plain term goes through.
        """
        conditions = []

        if value.lower is not None:
            operator = 'gte' if value.include_lower else 'gt'
            bound = convert(value.lower, value.position, False)
            conditions.append(self._bound_condition(column, operator, bound, json_path))

        if value.upper is not None:
            operator = 'lte' if value.include_upper else 'lt'
            bound = convert(value.upper, value.position, value.include_upper)
            conditions.append(self._bound_condition(column, operator, bound, json_path))

        if len(conditions) == 1:
            return conditions[0]
        return and_(*conditions)

    def _bound_condition(self, column, operator, bound, json_path):
        if json_path is None:
            return build_condition(column, operator, bound)
        return build_json_condition(column, json_path, operator, bound)

    def _require_ordered(self, descriptor, value):
        if descriptor.ordered:
            return
        raise SearchQueryError(
            f"'{descriptor.alias}' has no order, so > and ranges do not apply — "
            f'use {descriptor.alias}:value or {descriptor.alias}:(a OR b)',
            value.position,
        )

    def _as_integer(self, descriptor, value):
        return self._integer_text(descriptor, _unescape(value.text), value.position)

    def _integer_text(self, descriptor, text, position):
        try:
            return int(text)
        except ValueError:
            raise SearchQueryError(
                f"'{descriptor.alias}' takes a number, got '{text}'", position
            ) from None

    def _single_lookup(self, descriptor, value: TermValue):
        """One id for a comparison bound — `severity:>=High` needs exactly one."""
        identifiers = self._resolve_lookup(descriptor, value)
        if len(identifiers) > 1:
            raise SearchQueryError(
                f"'{_unescape(value.text)}' matches several {descriptor.alias} values — "
                'be more specific',
                value.position,
            )
        return identifiers[0]

    def _resolve_lookup(self, descriptor, value: TermValue):
        """Name(s) an analyst typed, as the ids the column holds.

        Exact first, then substring: `status:progress` should find "In
        progress" rather than telling an analyst who is three keystrokes
        from the answer that there is no such status. A wildcard skips
        straight to the pattern — writing one is a statement that you
        meant a pattern.
        """
        lookup = descriptor.lookup
        text = _unescape(value.text)
        cache_key = (descriptor.alias, text, value.quoted)
        cached = self._lookup_cache.get(cache_key)
        if cached is not None:
            return cached

        identifier = getattr(lookup.model, lookup.identifier)
        columns = [getattr(lookup.model, lookup.name)]
        columns.extend(getattr(lookup.model, name) for name in lookup.alternates)

        pattern, has_wildcard = _like_pattern(value)

        rows = []
        if not has_wildcard:
            exact = or_(*[func.lower(column) == text.lower() for column in columns])
            rows = db.session.query(identifier).filter(exact).all()

        if not rows:
            loose = pattern if has_wildcard else f'%{pattern}%'
            partial = or_(*[
                column.ilike(loose, escape=LIKE_ESCAPE_CHARACTER) for column in columns
            ])
            rows = db.session.query(identifier).filter(partial).all()

        if not rows:
            raise SearchQueryError(
                self._no_match_message(descriptor, text), value.position
            )

        identifiers = [row[0] for row in rows]
        self._lookup_cache[cache_key] = identifiers
        return identifiers

    def _no_match_message(self, descriptor, text):
        """Why nothing matched, with a suggestion when it is safe to give one.

        Customers are deliberately excluded: listing the names of clients
        the caller may not be entitled to would turn a typo into a way to
        enumerate other tenants.
        """
        message = f"No {descriptor.alias} matches '{text}'"
        lookup = descriptor.lookup
        if not lookup.suggestable:
            return message

        column = getattr(lookup.model, lookup.name)
        known = [row[0] for row in db.session.query(column).limit(200).all() if row[0]]
        suggestion = get_close_matches(text.lower(), [name.lower() for name in known],
                                       n=1, cutoff=0.6)
        if not suggestion:
            return message

        for name in known:
            if name.lower() == suggestion[0]:
                return f"{message} — did you mean '{name}'?"
        return message

    def _terminal_status_ids(self):
        return self._status_ids_named(TERMINAL_ALERT_STATUS_NAMES)

    def _status_ids_named(self, names):
        """Status ids for a set of names, matched case-insensitively.

        Statuses are seeded by name and editable, so `is:open` resolves
        them every time rather than hard-coding ids that a deployment may
        not have.
        """
        if self._status_cache is None:
            self._status_cache = {
                (row[1] or '').lower(): row[0]
                for row in db.session.query(
                    AlertStatus.status_id, AlertStatus.status_name
                ).all()
            }
        return [
            self._status_cache[name]
            for name in names
            if name in self._status_cache
        ]


def compile_alert_query(query: str, user_identifier=None):
    """Parse and compile `query` into one SQLAlchemy clause.

    Returns `None` when the expression is empty, which callers treat as
    "no query" rather than as "match nothing". Raises `SearchQueryError`,
    carrying a character offset, for anything the analyst needs to fix.
    """
    node = lucene_parse(query)
    if node is None:
        return None

    return AlertQueryCompiler(user_identifier).compile(node)
