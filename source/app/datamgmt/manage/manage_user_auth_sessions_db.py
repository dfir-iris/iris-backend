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

"""Query helpers for the `user_auth_session` table (VI-004).

Nothing here knows what a JWT is. The business layer owns the claims and
the reuse-detection policy; this module only answers three questions
about a token family: is it still live, what refresh token id does it
currently accept, and is it closed now.
"""

import uuid
from datetime import datetime

from app.db import db
from app.models.authorization import UserAuthSession


def user_auth_sessions_create(user_id: int, refresh_jti: str) -> str:
    """Open a token family for `user_id` and return its freshly minted `sid`."""
    auth_session = UserAuthSession(
        sid=str(uuid.uuid4()),
        user_id=user_id,
        refresh_jti=refresh_jti,
        last_used_at=datetime.utcnow()
    )
    db.session.add(auth_session)
    db.session.commit()

    return auth_session.sid


def user_auth_sessions_get_live(sid: str):
    """The family named by `sid` if it has not been revoked, else None.

    Deliberately a single probe on the unique index over `sid`: this is
    called once per authenticated request, so anything more than one
    lookup here is felt across the whole API.
    """
    if not sid:
        return None

    return UserAuthSession.query.filter(
        UserAuthSession.sid == sid,
        UserAuthSession.revoked_at.is_(None)
    ).first()


def user_auth_sessions_rotate(sid: str, refresh_jti: str) -> bool:
    """Make `refresh_jti` the only refresh token `sid` will accept.

    Returns False when the family is gone or already revoked, so the
    caller can refuse rather than quietly re-open a session someone has
    deliberately closed.
    """
    auth_session = user_auth_sessions_get_live(sid)
    if auth_session is None:
        return False

    auth_session.refresh_jti = refresh_jti
    auth_session.last_used_at = datetime.utcnow()
    db.session.commit()

    return True


def user_auth_sessions_revoke(sid: str) -> bool:
    """Close the family for good. Idempotent — re-revoking returns False.

    Every access token carrying this `sid` stops authenticating on its
    next request, which is what makes logout and reuse detection take
    effect immediately instead of after the access token's remaining
    lifetime.
    """
    auth_session = user_auth_sessions_get_live(sid)
    if auth_session is None:
        return False

    auth_session.revoked_at = datetime.utcnow()
    db.session.commit()

    return True
