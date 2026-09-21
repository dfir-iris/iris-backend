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

"""Query helpers for the `auth_throttle` table.

Storage only. The thresholds, the window length and the decision to lock
belong to the callers in `app.business` — this module counts and
remembers, it does not have an opinion about how much is too much.

The one thing that has to happen here rather than there is the increment.
Read-modify-write from four gunicorn workers against one row loses
counts, and a throttle that undercounts is a throttle an attacker can
outrun by opening more connections, so `auth_throttle_register_failure`
is a single `INSERT ... ON CONFLICT DO UPDATE` and the row lock Postgres
takes on the conflicting row is what serialises the four workers.
"""

from datetime import datetime
from datetime import timedelta

from sqlalchemy import text

from app.db import db
from app.models.authorization import AuthThrottle


# Rolls the window and bumps the count in one statement, and returns what
# the count became. The `CASE`s are what expire a window: a bucket last
# touched before the cutoff starts again at one rather than carrying
# stale failures forward forever.
_REGISTER_FAILURE = text("""
    INSERT INTO auth_throttle (key, failures, window_start, locked_until)
    VALUES (:key, 1, :now, NULL)
    ON CONFLICT (key) DO UPDATE SET
        failures = CASE WHEN auth_throttle.window_start < :cutoff
                        THEN 1
                        ELSE auth_throttle.failures + 1 END,
        window_start = CASE WHEN auth_throttle.window_start < :cutoff
                            THEN :now
                            ELSE auth_throttle.window_start END
    RETURNING failures
""")


def auth_throttle_register_failure(key: str, window_seconds: int) -> int:
    """Count one failure against `key` and return the running total.

    The total is for the current window only — a bucket whose window has
    expired restarts at 1 — so the caller can compare it against a ceiling
    without having to know when the window opened.
    """
    now = datetime.utcnow()
    failures = db.session.execute(_REGISTER_FAILURE, {
        'key': key,
        'now': now,
        'cutoff': now - timedelta(seconds=window_seconds),
    }).scalar()
    db.session.commit()

    return int(failures or 0)


def auth_throttle_lockout_seconds(key: str) -> int:
    """Seconds left on `key`'s lockout, or 0 when it may proceed.

    A single-column read on the primary key: this runs before the
    password is checked on every login attempt, so it has to stay one
    index probe.
    """
    row = db.session.query(AuthThrottle.locked_until).filter(
        AuthThrottle.key == key
    ).first()
    if row is None or row[0] is None:
        return 0

    remaining = (row[0] - datetime.utcnow()).total_seconds()

    return int(remaining) if remaining > 0 else 0


def auth_throttle_lock(key: str, lockout_seconds: int):
    """Lock `key` out for `lockout_seconds` and drop its failure history.

    Clearing the count with the lockout is deliberate and matches what the
    in-process throttles did: leaving a full window behind would re-lock
    the bucket on the first attempt after the lockout expired, so one
    burst would cost the account every subsequent window too.
    """
    locked_until = datetime.utcnow() + timedelta(seconds=lockout_seconds)
    db.session.query(AuthThrottle).filter(AuthThrottle.key == key).update(
        {'failures': 0, 'locked_until': locked_until},
        synchronize_session=False,
    )
    db.session.commit()


def auth_throttle_clear(key: str):
    """Forget `key` entirely. Used when a caller proves themselves."""
    db.session.query(AuthThrottle).filter(AuthThrottle.key == key).delete(
        synchronize_session=False
    )
    db.session.commit()


def auth_throttle_clear_all():
    """Drop every bucket. Test hook only."""
    db.session.query(AuthThrottle).delete(synchronize_session=False)
    db.session.commit()
