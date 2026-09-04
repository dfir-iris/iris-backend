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

Scope: in-process, per worker, same as the MFA throttle in
`blueprints/rest/v2/auth.py`. With N gunicorn workers an attacker gets N
times the budget, and a restart clears the state. That is still a real
bound on an attack that previously had none, and it keeps the login path
free of a Redis dependency. A shared backing store is the upgrade if a
deployment needs a hard guarantee.
"""

import threading
import time

from flask import request

from app import app


_ACCOUNT_MAX_FAILURES = 10
_CLIENT_MAX_FAILURES = 50
_WINDOW_SECONDS = 15 * 60
_LOCKOUT_SECONDS = 15 * 60

_lock = threading.Lock()
# bucket key -> {'failures': [monotonic timestamps], 'locked_until': monotonic}
_state = {}


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


def _remaining_lockout(key, now) -> int:
    entry = _state.get(key)
    if not entry:
        return 0

    locked_until = entry.get('locked_until', 0)
    if locked_until > now:
        return int(locked_until - now)

    return 0


def login_lockout_seconds(username) -> int:
    """Seconds the caller must wait, or 0 if the attempt may proceed."""
    now = time.monotonic()
    with _lock:
        return max(
            _remaining_lockout(_account_key(username), now),
            _remaining_lockout(_client_key(), now),
        )


def _register(key, ceiling, now):
    entry = _state.setdefault(key, {'failures': [], 'locked_until': 0})
    cutoff = now - _WINDOW_SECONDS
    entry['failures'] = [stamp for stamp in entry['failures'] if stamp > cutoff]
    entry['failures'].append(now)
    if len(entry['failures']) >= ceiling:
        entry['locked_until'] = now + _lockout_seconds()
        # Drop the history with the lockout: the lockout is the
        # deterrent, and keeping the window full would re-lock on the
        # first attempt after it expires.
        entry['failures'] = []


def register_login_failure(username):
    """Record one rejected password against both buckets."""
    now = time.monotonic()
    with _lock:
        _register(_account_key(username), _account_max_failures(), now)
        _register(_client_key(), _client_max_failures(), now)


def register_login_success(username):
    """Clear the account bucket after a valid password.

    The client bucket survives on purpose: an attacker spraying from one
    address who lands a single valid credential shouldn't reset the
    budget they've been burning on everyone else's account.
    """
    with _lock:
        _state.pop(_account_key(username), None)


def reset_login_throttle():
    """Drop all throttle state. Test hook."""
    with _lock:
        _state.clear()
