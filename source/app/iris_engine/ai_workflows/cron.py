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

"""Minimal 5-field cron expressions for scheduled workflows.

`minute hour day-of-month month day-of-week`, each field being `*`, a
number, a range `a-b`, a step `*/n` or `a-b/n`, or a comma list of
those. Months and week days also take three-letter names (`jan`,
`mon`); week day 0 and 7 are both Sunday. As in Vixie cron, when both
day fields are restricted a day matches if either does. Times are UTC.
"""

import datetime
from dataclasses import dataclass

_MONTH_NAMES = {name: i + 1 for i, name in enumerate(
    ('jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'))}
_DAY_NAMES = {name: i for i, name in enumerate(('sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat'))}

# (label, min, max, names)
_FIELDS = (
    ('minute', 0, 59, None),
    ('hour', 0, 23, None),
    ('day of month', 1, 31, None),
    ('month', 1, 12, _MONTH_NAMES),
    ('day of week', 0, 7, _DAY_NAMES),
)

# A tick that was missed (worker down) is caught up for this long, not more
_CATCH_UP_MINUTES = 60


@dataclass(frozen=True)
class CronSpec:
    minutes: frozenset
    hours: frozenset
    days: frozenset
    months: frozenset
    weekdays: frozenset
    days_restricted: bool
    weekdays_restricted: bool

    def matches(self, when) -> bool:
        if when.minute not in self.minutes or when.hour not in self.hours or when.month not in self.months:
            return False
        # Python: Monday is 0; cron: Sunday is 0
        weekday = (when.weekday() + 1) % 7
        day_ok = when.day in self.days
        weekday_ok = weekday in self.weekdays
        if self.days_restricted and self.weekdays_restricted:
            return day_ok or weekday_ok
        return day_ok and weekday_ok


def _value(token, label, low, high, names):
    token = token.strip().lower()
    if names and token in names:
        return names[token]
    if not token.isdigit():
        raise ValueError(f'Invalid {label} value "{token}"')
    value = int(token)
    if value < low or value > high:
        raise ValueError(f'{label.capitalize()} value {value} is out of range {low}-{high}')
    return value


def _parse_field(text, label, low, high, names) -> tuple:
    """(set of values, restricted?)"""
    values = set()
    restricted = True
    for part in text.split(','):
        if not part:
            raise ValueError(f'Empty item in {label} field')
        base, slash, step_text = part.partition('/')
        step = 1
        if slash:
            if not step_text.isdigit() or int(step_text) < 1:
                raise ValueError(f'Invalid step "{step_text}" in {label} field')
            step = int(step_text)
        if base == '*':
            start, end = low, high
            if not slash:
                restricted = False
        elif '-' in base:
            first, _, last = base.partition('-')
            start = _value(first, label, low, high, names)
            end = _value(last, label, low, high, names)
            if start > end:
                raise ValueError(f'Invalid range "{base}" in {label} field')
        else:
            start = _value(base, label, low, high, names)
            end = high if slash else start
        values.update(range(start, end + 1, step))
    return values, restricted


def ai_workflows_cron_parse(expr) -> CronSpec:
    """Parse a 5-field expression; ValueError with a readable message
    when it is not one."""
    if not isinstance(expr, str) or not expr.strip():
        raise ValueError('A cron expression is required')
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError(f'A cron expression has 5 fields (minute hour day month weekday), got {len(fields)}')
    parsed = [_parse_field(text, *spec) for text, spec in zip(fields, _FIELDS)]
    weekdays = {0 if v == 7 else v for v in parsed[4][0]}
    return CronSpec(
        minutes=frozenset(parsed[0][0]),
        hours=frozenset(parsed[1][0]),
        days=frozenset(parsed[2][0]),
        months=frozenset(parsed[3][0]),
        weekdays=frozenset(weekdays),
        days_restricted=parsed[2][1],
        weekdays_restricted=parsed[4][1],
    )


def ai_workflows_cron_is_due(expr, now, last_fired_at) -> bool:
    """Whether a schedule fired at some minute in (last_fired_at, now].

    Never fired: due when `now` itself matches. Missed minutes are caught
    up (once) for up to an hour, so a tick delayed by a busy worker does
    not skip a run, and a long outage does not replay a backlog."""
    spec = ai_workflows_cron_parse(expr)
    current = now.replace(second=0, microsecond=0)
    if last_fired_at is None:
        return spec.matches(current)
    last = last_fired_at.replace(second=0, microsecond=0)
    if last >= current:
        return False
    earliest = max(last + datetime.timedelta(minutes=1), current - datetime.timedelta(minutes=_CATCH_UP_MINUTES))
    probe = current
    while probe >= earliest:
        if spec.matches(probe):
            return True
        probe -= datetime.timedelta(minutes=1)
    return False
