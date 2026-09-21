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

"""Brute-force throttle on the API MFA endpoints.

A TOTP is six digits, so without a bound on attempts the second factor is
a million guesses away from nothing. `/auth/mfa-verify` and
`/auth/mfa-setup` share this counter — both accept a refresh token plus a
code, so throttling only the first would leave the second as a way to
spray combinations at the same account.

The legacy Jinja flow tracks fail count and lockout in the Flask session,
which the SPA has no equivalent of: it presents a bearer token and no
server-side session, so the count is keyed by user id instead.

This lived in `blueprints/rest/v2/auth.py` as a module-level dict, which
is per gunicorn worker — four workers, four counters, so the documented
5-attempt lockout cost an attacker twenty. It is now a row in
`auth_throttle`, shared with the password throttle in `login_throttle`.
It also has to live in the business layer rather than the blueprint,
because blueprints do not reach the persistence layer.
"""

from app.datamgmt.manage.manage_auth_throttle_db import auth_throttle_clear
from app.datamgmt.manage.manage_auth_throttle_db import auth_throttle_lock
from app.datamgmt.manage.manage_auth_throttle_db import auth_throttle_lockout_seconds
from app.datamgmt.manage.manage_auth_throttle_db import auth_throttle_register_failure


# Fixed rather than configurable, as they were before the counter moved
# out of process. `login_throttle` reads its ceilings off `app.config`
# because operators asked for them; nobody has asked for these, and the
# move to a shared store is not the moment to grow new public knobs.
_MFA_FAIL_THRESHOLD = 5
_MFA_WINDOW_SECONDS = 15 * 60
_MFA_LOCKOUT_SECONDS = 15 * 60


def _mfa_key(user_id) -> str:
    return f'mfa::{user_id}'


def mfa_lockout_seconds(user_id) -> int:
    """Seconds remaining in the lockout, or 0 if the user may attempt."""
    return auth_throttle_lockout_seconds(_mfa_key(user_id))


def register_mfa_failure(user_id):
    """Count one rejected code, and lock the account out at the ceiling."""
    key = _mfa_key(user_id)
    failures = auth_throttle_register_failure(key, _MFA_WINDOW_SECONDS)
    if failures >= _MFA_FAIL_THRESHOLD:
        auth_throttle_lock(key, _MFA_LOCKOUT_SECONDS)


def reset_mfa_throttle(user_id):
    """Forget the count. Called on a successful verify, and as a test hook."""
    auth_throttle_clear(_mfa_key(user_id))
