#  IRIS Source Code
#  Copyright (C) 2024 - DFIR-IRIS
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

import time
from urllib.parse import urlparse

from flask import flash
from flask import session
from flask import redirect
from flask import url_for
from flask import request
from flask_login import login_user

from app import bc
from app import app
from app.db import db
from app.business.cases import cases_get_by_identifier
from app.business.cases import cases_get_first
from app.logger import logger
from app.business.users import retrieve_user_by_username
from app.datamgmt.manage.manage_srv_settings_db import get_server_settings_as_dict
from app.datamgmt.manage.manage_user_auth_sessions_db import user_auth_sessions_create
from app.datamgmt.manage.manage_user_auth_sessions_db import user_auth_sessions_get_live
from app.datamgmt.manage.manage_user_auth_sessions_db import user_auth_sessions_revoke
from app.datamgmt.manage.manage_user_auth_sessions_db import user_auth_sessions_rotate
from app.iris_engine.access_control.ldap_handler import ldap_authenticate
from app.iris_engine.demo_builder import demo_mode_blocks_mfa
from app.iris_engine.access_control.utils import ac_get_effective_permissions_of_user
from app.iris_engine.utils.tracker import track_activity
from app.models.authorization import User
from app.models.errors import BusinessProcessingError

import datetime
import jwt
import uuid


def validate_ldap_login(username: str, password: str, local_fallback: bool = True):
    """
    Validate the user login using LDAP authentication.

    :param username: Username
    :param password: Password
    :param local_fallback: If True, will fall back to local authentication if LDAP fails.
    :return: User object if successful, None otherwise
    """
    try:
        if ldap_authenticate(username, password) is False:
            if local_fallback is True:
                track_activity(f'wrong login password for user \'{username}\' using LDAP auth - falling back to local based on settings',
                               ctx_less=True, display_in_ui=True)
                return validate_local_login(username, password)
            track_activity(f'wrong login password for user \'{username}\' using LDAP auth', ctx_less=True, display_in_ui=True)
            return None

        user = retrieve_user_by_username(username)
        if not user:
            return None
        return user
    except Exception as e:
        logger.error(e.__str__())
        return None


def validate_local_login(username: str, password: str):
    """
    Validate the user login using local authentication.

    :param username: Username
    :param password: Password

    :return: User object if successful, None otherwise
    """
    user = retrieve_user_by_username(username)
    if not user:
        return None

    if bc.check_password_hash(user.password, password):
        return user

    track_activity(f'wrong login password for user \'{username}\' using local auth', ctx_less=True, display_in_ui=True)
    return None


def _is_safe_url(target):
    """Return True iff `target` is safe to use in a 302 Location header.

    A safe target is a *relative* path on this application. The previous
    implementation only checked `parsed.scheme` and `parsed.netloc`, which is
    bypassed by payloads like `attacker.com?cid=1` — urlparse treats that as a
    path with an empty netloc, but browsers resolving a `Location: attacker.com`
    header will route the user to the attacker's host. That's GHSA-vjc3-7jwv-j9qf
    / SBA-ADV-20260126-02 / CWE-601.

    The strict rules:
      - non-empty string
      - no control characters (incl. tab/newline) or backslashes (some browsers
        normalise `\\` -> `/`, turning `/\\evil.com` into `//evil.com`)
      - starts with a single `/` (not `//`, which is protocol-relative)
      - urlparse confirms no scheme and no netloc — defense in depth
    """
    if not target or not isinstance(target, str):
        return False
    if any(ord(c) < 0x20 or c == '\\' for c in target):
        return False
    if not target.startswith('/') or target.startswith('//'):
        return False
    parsed = urlparse(target)
    return not parsed.scheme and not parsed.netloc


def _filter_next_url(next_url, context_case):
    """
    Ensures that the URL to which the user is redirected is safe. If the provided URL is not safe or is missing,
    a default URL (typically the index page) is returned.
    """
    if not _is_safe_url(next_url):
        return url_for('index.index', cid=context_case)
    return next_url


def mfa_is_enforced() -> bool:
    """Effective server-wide MFA policy.

    Reads `enforce_mfa` off the cached settings row, except in demo
    mode where MFA is forced off whatever the row says: demo accounts
    are shared, so a second factor bound to one visitor's authenticator
    locks everyone else out. Every reader of the policy must go through
    here so the demo override can't be bypassed by one forgotten call
    site.
    """
    if demo_mode_blocks_mfa():
        return False

    if 'SERVER_SETTINGS' not in app.config:
        app.config['SERVER_SETTINGS'] = get_server_settings_as_dict()

    return bool(app.config['SERVER_SETTINGS'].get('enforce_mfa'))


def wrap_login_user(user, is_oidc=False):

    session['username'] = user.user

    if mfa_is_enforced() and is_oidc is False:
        # MFA state must be bound to the specific user who verified — a flat
        # boolean would let a prior verified session admit a different user on
        # the same browser (shared device, attacker knows user B's password
        # and reuses user A's mfa_verified=True). Backport of f596481b.
        verified_for = session.get('mfa_verified_for_user_id')
        if verified_for != user.id:
            # If the session is currently MFA-locked out, do NOT reset state
            # here — an attacker who re-POSTs /login mustn't be able to zero
            # out the fail counter and get a fresh burst of tokens.
            locked_until = session.get('mfa_lockout_until')
            if locked_until and locked_until > time.time():
                flash('Too many attempts. Please try again later.', 'danger')
                return redirect(url_for('login.login'))

            # Mark this browser session as the one that just passed password
            # auth for this user. mfa_setup / mfa_verify will refuse to run
            # for any other user id, preventing cross-user MFA handler abuse.
            session['pre_mfa_user_id'] = user.id
            session['mfa_fail_count'] = 0
            session.pop('mfa_lockout_until', None)
            session.pop('pending_mfa_secret', None)
            return redirect(url_for('mfa_verify'))

    login_user(user)

    update_session_current_case(user)

    track_activity(f'user \'{user.user}\' successfully logged-in', ctx_less=True)

    next_url = _filter_next_url(request.args.get('next'), user.ctx_case)
    return redirect(next_url)


def update_session_current_case(user: User):
    session['permissions'] = ac_get_effective_permissions_of_user(user)

    if user.ctx_case is None:
        case = cases_get_first()
        user.ctx_case = case.case_id
        db.session.commit()

    case = cases_get_by_identifier(user.ctx_case)

    session['current_case'] = {
        'case_name': case.name,
        'case_info': '',
        'case_id': user.ctx_case
    }


def _mfa_required_for_user(user) -> bool:
    """Whether tokens minted for `user` must carry verified-MFA state.

    Deliberately independent of whether the account has *completed*
    enrollment. Gating on `mfa_setup_complete` meant an MFA-enforced
    deployment handed an unenrolled account a token flagged
    `mfa_required=False, mfa_verified=True` — i.e. a full-access bearer
    token obtained by skipping the very enrollment the policy exists to
    require. An attacker holding only the first factor could log in,
    ignore the SPA's enrollment redirect, and use the token directly
    (VI-003).

    Enrollment state still matters, but for routing rather than
    admission: `_mfa_status_for` reports it so the SPA knows whether to
    show mfa-setup or mfa-verify. Both screens are reachable with a
    step-1 token because `/auth/mfa-setup` and `/auth/mfa-verify`
    authenticate off the refresh token rather than the API guard.

    `user` is unused today — the policy is server-wide — but is kept in
    the signature since every call site has the user to hand and a
    per-account exemption would land here.
    """
    return mfa_is_enforced()


def auth_session_is_live(session_id) -> bool:
    """Whether `session_id` still names an unrevoked token family.

    This is the cheap liveness question — "has this session been logged
    out?" — as opposed to `auth_session_accepts_refresh`, which also
    cares *which* refresh token is being presented. Callers that merely
    authenticate a bearer (the access-token gate, the MFA endpoints that
    read a refresh token only to learn who is asking) want this one.

    An absent `session_id` is a token minted before session tracking
    existed. Refused here rather than left to the query helper, because
    "old tokens are not grandfathered" is a policy decision and belongs
    where the policy is.
    """
    if not session_id:
        return False

    return user_auth_sessions_get_live(session_id) is not None


def auth_session_accepts_refresh(session_id, refresh_jti) -> bool:
    """Whether this exact refresh token may still be exchanged (VI-004).

    Three ways to fail, and the third is the interesting one:

      - no `session_id` at all. Tokens minted before session tracking
        existed carry no `sid`, and they are refused rather than
        grandfathered — upgrading logs everyone out, which is the only
        honest outcome for a fix whose whole point is that outstanding
        credentials could not previously be invalidated.
      - the family is unknown or already revoked (logged out, or killed
        by an earlier reuse).
      - the family is live but the caller presents a `jti` we have
        already rotated away from. That token was spent, so somebody is
        replaying a copy — and since the legitimate holder and the thief
        present indistinguishable credentials, the only containment that
        works is to revoke the entire family and make both parties log
        in again. This is the standard OAuth reuse-detection response.

    Note this deliberately does *not* consume the token: rotation
    happens in `generate_auth_tokens`, so an endpoint that reads a
    refresh token without issuing a replacement (mfa-verify, mfa-setup)
    can validate without tripping the reuse check on the next genuine
    refresh.
    """
    if not session_id:
        return False

    auth_session = user_auth_sessions_get_live(session_id)
    if auth_session is None:
        return False

    if not refresh_jti or auth_session.refresh_jti != refresh_jti:
        user_auth_sessions_revoke(session_id)
        track_activity('authentication session revoked: a rotated-out refresh token was replayed',
                       ctx_less=True, display_in_ui=True,
                       user_id_override=auth_session.user_id)
        return False

    return True


def auth_session_revoke(session_id) -> bool:
    """Close a token family. No-op when there is nothing to close.

    Used by logout, and by mfa-verify to retire the step-1 session it
    replaces.
    """
    if not session_id:
        return False

    return user_auth_sessions_revoke(session_id)


def generate_auth_tokens(user, mfa_verified: bool = False, session_id: str = None):
    """
    Generate access and refresh tokens with essential user data

    :param user: User object
    :param mfa_verified: Whether the holder has already cleared the second factor
    :param session_id: Existing token family to rotate. None opens a new one.
    :return: Dict containing tokens with expiry
    """
    # Configure token expiration times
    access_token_expiry = datetime.datetime.now(datetime.UTC) + datetime.timedelta(
        minutes=app.config.get('ACCESS_TOKEN_EXPIRES_MINUTES', 15)
    )
    refresh_token_expiry = datetime.datetime.now(datetime.UTC) + datetime.timedelta(
        days=app.config.get('REFRESH_TOKEN_EXPIRES_DAYS', 14)
    )

    mfa_required = _mfa_required_for_user(user)
    effective_mfa_verified = True if not mfa_required else bool(mfa_verified)

    # Bind both tokens to a server-side row so they can be revoked (VI-004).
    # The row is created here rather than at each call site so that every
    # entry point — local login, LDAP login, OIDC exchange, mfa-verify,
    # refresh — gets a session without having to remember to ask for one.
    refresh_jti = str(uuid.uuid4())
    if session_id is None:
        session_id = user_auth_sessions_create(user.id, refresh_jti)
    elif not user_auth_sessions_rotate(session_id, refresh_jti):
        # The family died between the caller's check and this write — a
        # concurrent logout, or reuse detection firing on a parallel
        # request. Refusing is the only safe answer: falling back to a
        # fresh session would hand the caller exactly the credential the
        # revocation was meant to take away.
        raise BusinessProcessingError('Authentication session is no longer valid')

    # Generate access token with user data
    access_token_payload = {
        'user_id': user.id,
        'user_name': user.name,
        'user_email': user.email,
        'user_login': user.user,
        'type': 'access',
        'sid': session_id,
        'mfa_required': mfa_required,
        'mfa_verified': effective_mfa_verified,
        'exp': access_token_expiry
    }
    access_token = jwt.encode(
        access_token_payload,
        app.config.get('SECRET_KEY'),
        algorithm='HS256'
    )

    # Generate refresh token. The MFA flags travel with the refresh too so
    # the refresh endpoint can mint new access tokens that preserve the
    # caller's MFA state without re-prompting — and, crucially, without
    # silently upgrading a step-1 refresh into a verified access token.
    # `jti` is what distinguishes this refresh token from the ones the
    # same family issued before it; the refresh endpoint only honours the
    # most recent.
    refresh_token_payload = {
        'user_id': user.id,
        'user_name': user.name,
        'user_email': user.email,
        'user_login': user.user,
        'exp': refresh_token_expiry,
        'type': 'refresh',
        'sid': session_id,
        'jti': refresh_jti,
        'mfa_required': mfa_required,
        'mfa_verified': effective_mfa_verified,
    }
    refresh_token = jwt.encode(
        refresh_token_payload,
        app.config.get('SECRET_KEY'),
        algorithm='HS256'
    )

    return {
        'access_token': access_token,
        'refresh_token': refresh_token,
        'access_token_expires_at': access_token_expiry.timestamp(),
        'refresh_token_expires_at': refresh_token_expiry.timestamp()
    }


def validate_auth_token(token):
    """
    Validate an authentication token

    Signature and expiry alone are not enough: an access token also has
    to belong to a session that is still open (VI-004). That costs one
    indexed lookup on a path that used to be entirely stateless, which is
    a real price to pay per authenticated request — but the alternative
    is the finding itself, a logout that leaves the caller's outstanding
    access token working for up to another fifteen minutes. The lookup is
    a single probe on a unique index and nothing else may be added here.

    :param token: JWT token to validate
    :return: Dict with user data if valid, None otherwise
    """
    try:
        payload = jwt.decode(token, app.config.get('SECRET_KEY'), algorithms=['HS256'])

        if payload.get('type') != 'access':
            return None

        # No `sid` means the token predates session tracking. Rejected
        # rather than grandfathered — everyone is logged out once by the
        # upgrade, which is the intended cost of a fix that exists
        # precisely because old tokens could not be invalidated.
        session_id = payload.get('sid')
        if not auth_session_is_live(session_id):
            return None

        return {
            'user_id': payload.get('user_id'),
            'user_login': payload.get('user_login'),
            'user_name': payload.get('user_name'),
            'user_email': payload.get('user_email'),
            'session_id': session_id,
            'mfa_required': payload.get('mfa_required', False),
            'mfa_verified': payload.get('mfa_verified', False),
        }
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None
