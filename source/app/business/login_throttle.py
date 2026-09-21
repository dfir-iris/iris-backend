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

"""Application-level throttle on password authentication (VI-010).

Both login surfaces — the SPA's `POST /api/v2/auth/login` and the legacy
Jinja `POST /login` — validated every submitted password and answered
"Invalid credentials" with nothing in between, so credential stuffing and
password spraying ran at network speed against local and LDAP accounts.
Deployments that terminate at a WAF were covered; deployments that don't
were not, and the application couldn't tell which it was.

Two independent buckets, because they catch different attacks:

* **account** — many passwords against one username (brute force). Tight
  threshold; a legitimate user rarely fumbles ten times in a row.
* **client** — many usernames from one address (spraying). Looser, since
  an office behind one NAT gateway shares a source address; the point is
  to bound the rate, not to lock a whole site out on the first typo.

A hit on either bucket refuses the attempt *before* the password is
checked, so a locked-out account can't be probed for password validity
through response timing either.

Scope: the counters live in the `auth_throttle` table, shared by every
worker. They used to be a module-level dict, which is per OS process —
with `gunicorn -w 4` an attacker got four independent budgets and a
restart handed all four back, so the documented ceilings were not the
ceilings anyone actually got. The store is Postgres rather than Redis
because the login path already writes a `UserActivity` row per rejected
attempt (`iris_engine/utils/tracker.py`), so the write was being paid
anyway, and because a new service on a release that already ships six of
them is a poor trade for a counter. `mfa_throttle` shares the table.
"""

from flask import request

from app import app
from app.datamgmt.manage.manage_auth_throttle_db import auth_throttle_clear
from app.datamgmt.manage.manage_auth_throttle_db import auth_throttle_clear_all
from app.datamgmt.manage.manage_auth_throttle_db import auth_throttle_lock
from app.datamgmt.manage.manage_auth_throttle_db import auth_throttle_lockout_seconds
from app.datamgmt.manage.manage_auth_throttle_db import auth_throttle_register_failure


_ACCOUNT_MAX_FAILURES = 10
_CLIENT_MAX_FAILURES = 50
_WINDOW_SECONDS = 15 * 60
_LOCKOUT_SECONDS = 15 * 60


def _lockout_seconds() -> int:
    return int(app.config.get('LOGIN_LOCKOUT_SECONDS') or _LOCKOUT_SECONDS)


def _account_max_failures() -> int:
    return int(app.config.get('LOGIN_MAX_ATTEMPTS') or _ACCOUNT_MAX_FAILURES)


def _client_max_failures() -> int:
    return int(app.config.get('LOGIN_MAX_ATTEMPTS_PER_CLIENT') or _CLIENT_MAX_FAILURES)


def _client_key() -> str:
    """Bucket key for the caller's address.

    `request.remote_addr` is the peer as Flask sees it. Behind a reverse
    proxy that is the proxy, which collapses every client into one
    bucket — the reason the client threshold is deliberately loose and
    the account bucket carries the real weight. Deployments that trust
    their proxy should configure Werkzeug's ProxyFix so `remote_addr`
    reflects the real client.
    """
    if not request:
        return 'client::unknown'
    return f'client::{request.remote_addr or "unknown"}'


def _account_key(username) -> str:
    return f'account::{str(username or "").strip().lower()}'


def login_lockout_seconds(username) -> int:
    """Seconds the caller must wait, or 0 if the attempt may proceed."""
    return max(
        auth_throttle_lockout_seconds(_account_key(username)),
        auth_throttle_lockout_seconds(_client_key()),
    )


def _register(key, ceiling):
    failures = auth_throttle_register_failure(key, _WINDOW_SECONDS)
    if failures >= ceiling:
        auth_throttle_lock(key, _lockout_seconds())


def register_login_failure(username):
    """Record one rejected password against both buckets."""
    _register(_account_key(username), _account_max_failures())
    _register(_client_key(), _client_max_failures())


def register_login_success(username):
    """Clear the account bucket after a valid password.

    The client bucket survives on purpose: an attacker spraying from one
    address who lands a single valid credential shouldn't reset the
    budget they've been burning on everyone else's account.
    """
    auth_throttle_clear(_account_key(username))


def reset_login_throttle():
    """Drop all throttle state. Test hook."""
    auth_throttle_clear_all()
