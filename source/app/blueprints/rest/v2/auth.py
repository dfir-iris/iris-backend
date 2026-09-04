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

import threading
import time

import jwt
import pyotp

from flask import Blueprint
from flask import session
from flask import redirect
from flask import url_for
from flask import request
from flask import g
from flask_login import logout_user
from oic.oauth2.exception import GrantError

from app import app
from app import bc
from app.db import db
from app import oidc_client
from app.blueprints.iris_user import iris_current_user
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError
from app.logger import logger
from app.blueprints.access_controls import is_authentication_ldap
from app.blueprints.access_controls import is_authentication_oidc
from app.blueprints.access_controls import not_authenticated_redirection_url
from app.blueprints.rest.api_auth import api_auth
from app.blueprints.rest.api_doc import api_doc
from app.blueprints.rest.endpoints import response_api_error, response_api_not_found
from app.blueprints.rest.endpoints import response_api_success
from app.business.auth import validate_ldap_login
from app.business.auth import validate_local_login
from app.business.users import users_get_active
from app.business.auth import auth_session_accepts_refresh
from app.business.auth import auth_session_is_live
from app.business.auth import auth_session_revoke
from app.business.auth import generate_auth_tokens
from app.business.auth import mfa_is_enforced
from app.business.login_throttle import login_lockout_seconds
from app.business.login_throttle import register_login_failure
from app.business.login_throttle import register_login_success
from app.iris_engine.demo_builder import demo_mode_blocks_mfa
from app.iris_engine.utils.tracker import track_activity
from app.schema.marshables import UserSchema


auth_blueprint = Blueprint('auth', __name__, url_prefix='/auth')


# Name of the HttpOnly cookie the SPA stores its refresh token in. Kept in
# sync with `COOKIE_REFRESH_TOKEN` in the frontend's
# `src/lib/server/token-cookies.ts`.
_REFRESH_TOKEN_COOKIE = 'iris_rt'


def _read_refresh_token(data):
    """Pull the refresh token out of the request body, else the cookie.

    The SPA never holds its refresh token: it lives in an HttpOnly cookie
    that JS cannot read, so the browser posts an empty body and expects
    something in front to splice the value in. The SvelteKit node server
    does exactly that (`hooks.server.ts` → `injectRefreshToken`), but it
    only gets the chance when it is actually in the request path.

    Deployments that route the API straight at this backend bypass it —
    iris-plane's Traefik config sends `/api/` to the Flask container at
    priority 100 while the node server serves everything else, so the
    body arrives as `{}` and the cookie arrives unread. Falling back to
    the cookie here makes the endpoint correct under either topology
    rather than depending on a proxy being deployed in front.

    Reading the credential from a cookie is safe against CSRF here: the
    cookie is set `SameSite=Lax`, so a cross-site POST does not carry it,
    and a same-site forgery could at most rotate the victim's own tokens
    — the response is not readable cross-origin.
    """
    token = (data or {}).get('refresh_token')
    if token:
        return token

    return request.cookies.get(_REFRESH_TOKEN_COOKIE)


# Per-user brute-force throttle on the API MFA endpoints. The legacy
# pages flow tracks fail count + lockout in the Flask session, but the
# SPA presents a bearer token without a session, so we keep an in-process
# counter keyed by user_id. After `_MFA_FAIL_THRESHOLD` consecutive bad
# tokens we refuse all attempts for `_MFA_LOCKOUT_SECONDS` regardless of
# password validity — making the 6-digit TOTP space (10^6) infeasible to
# brute-force. The counter resets on a successful verify or after the
# lockout expires. For multi-worker deployments this is per-worker, but
# the throttle on any single worker still raises the cost meaningfully.
_MFA_FAIL_THRESHOLD = 5
_MFA_LOCKOUT_SECONDS = 15 * 60
_mfa_throttle_lock = threading.Lock()
_mfa_throttle = {}


def _mfa_throttle_check(user_id):
    """Return seconds remaining in the lockout, or 0 if the user can attempt."""
    with _mfa_throttle_lock:
        entry = _mfa_throttle.get(user_id)
        if not entry:
            return 0
        if entry.get('locked_until', 0) > time.time():
            return int(entry['locked_until'] - time.time())
        return 0


def _mfa_throttle_register_failure(user_id):
    with _mfa_throttle_lock:
        entry = _mfa_throttle.setdefault(user_id, {'fail_count': 0, 'locked_until': 0})
        entry['fail_count'] = entry.get('fail_count', 0) + 1
        if entry['fail_count'] >= _MFA_FAIL_THRESHOLD:
            entry['locked_until'] = time.time() + _MFA_LOCKOUT_SECONDS
            # Reset the counter so the next post-lockout attempt isn't
            # immediately locked again — the lockout is the deterrent.
            entry['fail_count'] = 0


def _mfa_throttle_reset(user_id):
    with _mfa_throttle_lock:
        _mfa_throttle.pop(user_id, None)


def _mfa_status_for(user):
    """
    Compact MFA hint pair the SPA uses to pick its post-login route.

    `mfa_required` mirrors the effective server-wide policy (`enforce_mfa`,
    forced off in demo mode — see `mfa_is_enforced`) so the client can
    distinguish "user hasn't set up MFA because policy is off" from
    "must set up MFA before reaching the app". `mfa_setup_complete`
    reflects the user's row. OIDC users bypass MFA entirely (handled in
    `wrap_login_user`); for the local/LDAP login surface that calls this
    helper, the pair is sufficient to route to mfa-setup vs mfa-verify vs
    straight into the app.
    """
    return {
        'mfa_required': mfa_is_enforced(),
        'mfa_setup_complete': bool(getattr(user, 'mfa_setup_complete', False)),
    }


@auth_blueprint.post('/login')
@api_doc(tags=['Auth'], summary='Log in')
def login():
    """
    Login endpoint. Handles taking user/pass combo and authenticating a local session or returning an error.
    """
    if iris_current_user.is_authenticated:
        logger.info('User already authenticated - redirecting')
        logger.debug(f'User {iris_current_user.user} already logged in')
        user = users_get_active(iris_current_user.id)
        result = UserSchema(exclude=['user_password', 'mfa_secrets', 'webauthn_credentials']).dump(user)
        result.update(_mfa_status_for(user))
        return response_api_success(result)

    if is_authentication_oidc() and app.config.get('AUTHENTICATION_LOCAL_FALLBACK') is False:
        return redirect(url_for('login.oidc_login'))

    data = request.get_json(silent=True) or {}
    username = data.get('username')
    password = data.get('password')

    # Refuse before the password is checked (VI-010) — a throttled account
    # must not be probeable through the response, timing included.
    lockout = login_lockout_seconds(username)
    if lockout:
        track_activity(f'Throttled login attempt for user {username}', ctx_less=True, display_in_ui=True)
        return response_api_error(f'Too many login attempts. Try again in {lockout} seconds')

    if is_authentication_ldap() is True:
        authed_user = validate_ldap_login(username, password, app.config.get('AUTHENTICATION_LOCAL_FALLBACK'))

    else:
        authed_user = validate_local_login(username, password)

    if authed_user is None:

        register_login_failure(username)
        track_activity(f'User {username} tried to login. Invalid credentials', ctx_less=True, display_in_ui=True)
        return response_api_error('Invalid credentials')

    register_login_success(username)

    user_data = UserSchema(exclude=['user_password', 'mfa_secrets', 'webauthn_credentials']).dump(authed_user)

    # Generate auth tokens for API access. When MFA is required but not yet
    # verified, the access token carries `mfa_verified=False` and the SPA must
    # route to mfa-setup / mfa-verify before the token unlocks anything beyond
    # the MFA endpoints.
    tokens = generate_auth_tokens(authed_user)
    user_data.update({'tokens': tokens})
    user_data.update(_mfa_status_for(authed_user))

    track_activity(f'User {username} logged in', ctx_less=True)
    return response_api_success(data=user_data)


@auth_blueprint.post('/oidc-exchange')
@api_doc(tags=['Auth'], summary='Exchange OIDC session for JWT tokens')
def oidc_exchange():
    """
    Trade a fresh OIDC-authenticated session cookie for JWT access/refresh
    tokens, then invalidate the session so the cookie can't be reused.

    Security model:
      - Only redeemable when the Flask session was created by a successful
        OIDC callback in this same session (checked via the one-time
        `oidc_authenticated` flag set in login.oidc_authorise). A plain
        local-login session, or a session hydrated some other way, is
        rejected — this endpoint is not a general session->JWT converter.
      - Single-use: the flag is popped and the session is fully cleared
        before the response is returned, so the same session cookie
        cannot mint a second set of tokens even if the browser replays.
      - MFA is trusted from the IdP for OIDC users (mirrors wrap_login_user's
        is_oidc=True branch, which skips IRIS's local MFA prompt). Tokens
        are minted with mfa_verified=True — same policy as the existing
        session-based OIDC login has today.
    """
    if not is_authentication_oidc():
        return response_api_error('OIDC authentication is not enabled', status=400)

    if not iris_current_user.is_authenticated:
        return response_api_error('Unauthorized', status=401)

    if not session.pop('oidc_authenticated', False):
        # The user has a valid session but it wasn't created via the OIDC
        # callback (or the marker has already been consumed by a prior
        # exchange). Do NOT mint tokens.
        return response_api_error('No pending OIDC exchange for this session', status=403)

    user = users_get_active(iris_current_user.id)
    if user is None:
        session.clear()
        return response_api_error('User not active', status=403)

    user_data = UserSchema(
        exclude=['user_password', 'mfa_secrets', 'webauthn_credentials']
    ).dump(user)

    # OIDC users' MFA is enforced at the IdP. Match wrap_login_user's
    # is_oidc branch and mint tokens with mfa_verified=True so the SPA
    # doesn't route them through IRIS's local MFA prompt.
    tokens = generate_auth_tokens(user, mfa_verified=True)
    user_data.update({'tokens': tokens})
    user_data.update(_mfa_status_for(user))

    # Invalidate the bridge cookie: log the flask-login user out and
    # wipe every session key. From this point on the SPA authenticates
    # solely via the JWT it just received.
    logout_user()
    session.clear()

    track_activity(
        f'User {user.user} exchanged OIDC session for JWT tokens',
        ctx_less=True, display_in_ui=False,
    )

    return response_api_success(data=user_data)


@auth_blueprint.post('/mfa-setup')
@api_doc(tags=['Auth'], summary='Set up MFA')
def mfa_setup():
    """
    Persist user's MFA secret after validating:
      - refresh_token is valid (used to identify user_id)
      - provided TOTP token matches provided secret
      - provided password matches user (LDAP or local)
    """
    data = request.get_json(silent=True) or {}

    refresh_token = _read_refresh_token(data)
    token = data.get('token')
    mfa_secret = data.get('mfa_secret')
    user_password = data.get('user_password') or data.get('password')

    if not refresh_token or not token or not mfa_secret or not user_password:
        return response_api_error('Missing required fields: refresh_token, token, mfa_secret, password')

    try:
        payload = jwt.decode(refresh_token, app.config.get('SECRET_KEY'), algorithms=['HS256'])

        if payload.get('type') != 'refresh':
            return response_api_error('Invalid token type')

        user_id = payload.get('user_id')

        # Reuse the verify throttle so a leaked refresh token can't be used
        # to spray password/token combos against /mfa-setup either.
        remaining = _mfa_throttle_check(user_id)
        if remaining > 0:
            return response_api_error(
                f'Too many MFA attempts. Try again in {remaining} seconds.'
            )

        # A refresh token from a logged-out session must not still buy
        # the right to bind an authenticator to the account (VI-004).
        # Liveness only — enrollment issues no tokens, so there is
        # nothing here to rotate or retire, and checking the `jti` would
        # only risk mistaking a client holding a slightly stale copy for
        # an attacker.
        if not auth_session_is_live(payload.get('sid')):
            return response_api_error('Invalid refresh token')

        user = users_get_active(user_id)

        # Demo mode: refuse to bind an MFA secret to any account. The
        # demo landing page publishes the seeded credentials for every
        # visitor and accounts created during a session are just as
        # shared — letting one visitor enroll their own authenticator
        # would lock everyone else out.
        if demo_mode_blocks_mfa():
            track_activity(
                f"Refused MFA setup for user {user.user}: demo mode.",
                ctx_less=True,
                display_in_ui=True,
            )
            return response_api_error('MFA is disabled in demo mode')

        # Refuse to overwrite an existing enrollment from this endpoint.
        # Re-running setup with a fresh secret would silently invalidate
        # the legitimate user's authenticator app — an attacker holding
        # password + step-1 refresh token could lock the user out and
        # bind MFA to their own device. Resetting MFA must go through
        # the admin user-management flow.
        if user.mfa_setup_complete:
            track_activity(
                f"Refused MFA setup for user {user.user}: already enrolled.",
                ctx_less=True,
                display_in_ui=True,
            )
            return response_api_error('MFA already configured for this account')

        totp = pyotp.TOTP(mfa_secret)
        if not totp.verify(str(token)):
            _mfa_throttle_register_failure(user_id)
            track_activity(
                f"Failed MFA setup for user {user.user}. Invalid token.",
                ctx_less=True,
                display_in_ui=True,
            )
            return response_api_error('Invalid token')

        has_valid_password = False

        if is_authentication_ldap() is True:
            if validate_ldap_login(
                user.user,
                user_password,
                local_fallback=app.config.get("AUTHENTICATION_LOCAL_FALLBACK"),
            ):
                has_valid_password = True
        else:
            if bc.check_password_hash(user.password, user_password):
                has_valid_password = True

        if not has_valid_password:
            _mfa_throttle_register_failure(user_id)
            track_activity(
                f"Failed MFA setup for user {user.user}. Invalid password.",
                ctx_less=True,
                display_in_ui=True,
            )
            return response_api_error('Invalid password')

        user.mfa_secrets = mfa_secret
        user.mfa_setup_complete = True
        db.session.commit()
        _mfa_throttle_reset(user_id)

        track_activity(
            f"MFA setup successful for user {user.user}",
            ctx_less=True,
            display_in_ui=True,
        )

        return response_api_success({'mfa_setup_complete': True})

    except ObjectNotFoundError:
        return response_api_not_found()
    except jwt.ExpiredSignatureError:
        return response_api_error('Refresh token has expired')
    except jwt.InvalidTokenError:
        return response_api_error('Invalid refresh token')


@auth_blueprint.post('/mfa-verify')
@api_doc(tags=['Auth'], summary='Verify MFA token')
def mfa_verify():
    """
    Verify a TOTP token against the saved MFA secret.
    Uses refresh_token to identify the user (no reliance on iris_current_user).
    """
    data = request.get_json(silent=True) or {}

    refresh_token = _read_refresh_token(data)
    token = data.get('token')

    if not refresh_token or not token:
        return response_api_error('Missing required fields: refresh_token, token')

    try:
        payload = jwt.decode(refresh_token, app.config.get('SECRET_KEY'), algorithms=['HS256'])

        if payload.get('type') != 'refresh':
            return response_api_error('Invalid token type')

        user_id = payload.get('user_id')

        # Throttle BEFORE the DB lookup. Even invalid attempts must count
        # toward the lockout — otherwise an attacker could keep the user_id
        # claim fixed and brute-force the TOTP space at network speed.
        remaining = _mfa_throttle_check(user_id)
        if remaining > 0:
            return response_api_error(
                f'Too many MFA attempts. Try again in {remaining} seconds.'
            )

        # Liveness only, deliberately: this endpoint reads the refresh
        # token to learn who is asking, it does not spend it. Treating
        # the presentation as a rotation would make the ordinary
        # login → mfa-verify → refresh sequence look like a replay of a
        # rotated-out token and revoke the user's own session (VI-004).
        session_id = payload.get('sid')
        if not auth_session_is_live(session_id):
            return response_api_error('Invalid refresh token')

        user = users_get_active(user_id)

        if not user.mfa_secrets or not user.mfa_setup_complete:
            return response_api_error('MFA setup required')

        totp = pyotp.TOTP(user.mfa_secrets)
        if not totp.verify(str(token), valid_window=1):
            _mfa_throttle_register_failure(user_id)
            track_activity(
                f"Failed MFA verification for user {user.user}. Invalid token.",
                ctx_less=True,
                display_in_ui=True,
            )
            return response_api_error('Invalid token')

        _mfa_throttle_reset(user_id)
        track_activity(
            f"MFA verification successful for user {user.user}",
            ctx_less=True,
            display_in_ui=True,
        )

        # The verified pair opens a *new* family, and the step-1 family
        # that got the caller this far is retired on the spot. Leaving it
        # live would keep a `mfa_verified=False` refresh token valid for
        # its full fourteen days alongside the verified one, and every
        # retry of the login → verify loop would strand another orphan
        # row. What makes that step-1 credential harmless in the meantime
        # is the VI-003 gate; what makes it *gone* is this revocation.
        tokens = generate_auth_tokens(user, mfa_verified=True)
        auth_session_revoke(session_id)

        return response_api_success({'mfa_verified': True, 'tokens': tokens})

    except ObjectNotFoundError:
        return response_api_not_found()
    except jwt.ExpiredSignatureError:
        return response_api_error('Refresh token has expired')
    except jwt.InvalidTokenError:
        return response_api_error('Invalid refresh token')


@auth_blueprint.get('/whoami')
@api_auth()
@api_doc(tags=['Auth'], summary='Get the current user')
def whoami():
    """
    Returns current authenticated user info (based on the existing session) and API tokens.
    Output shape matches the frontend's existing local-login handler:
      { responseData, tokenInfo, redirectTo }
    """
    user = g.api_user

    response_data = UserSchema(
        exclude=['user_password', 'mfa_secrets', 'webauthn_credentials']
    ).dump(user)

    return response_api_success(data={
        'responseData': response_data,
        'tokenInfo': None,
        'redirectTo': '/'
    })


@auth_blueprint.post('/logout')
@api_auth()
@api_doc(tags=['Auth'], summary='Log out')
def logout():
    """
    Logout function. Erase its session and redirect to index i.e login
    :return: Page
    """

    if session.get('current_case'):
        iris_current_user.ctx_case = session['current_case']['case_id']
        db.session.commit()

    logout_username = g.api_user.user

    # A logout that revokes nothing is not a logout (VI-004). Closing the
    # family here kills the refresh token *and* the access token that was
    # used to make this very call — the per-request gate refuses a token
    # whose session is revoked, so the up-to-fifteen-minutes of remaining
    # access-token life stops being usable immediately rather than
    # outliving the user's decision to leave. Sessions authenticated by
    # cookie or API key carry no `sid` and simply have nothing to close.
    auth_session_revoke(g.auth_session_id if 'auth_session_id' in g else None)

    if is_authentication_oidc():
        if oidc_client.provider_info.get('end_session_endpoint'):
            try:
                logout_request = oidc_client.construct_EndSessionRequest(
                    state=session['oidc_state'])
                logout_url = logout_request.request(
                    oidc_client.provider_info["end_session_endpoint"])
                track_activity(f'user \'{logout_username}\' has been logged-out',
                               ctx_less=True, display_in_ui=True)
                logout_user()
                session.clear()
                return redirect(logout_url)
            except GrantError:
                track_activity(
                    f'no oidc session found for user \'{logout_username}\', skipping oidc provider logout and continuing to logout local user',
                    ctx_less=True,
                    display_in_ui=False
                )

    track_activity(f'user \'{logout_username}\' has been logged-out',
                   ctx_less=True, display_in_ui=True)
    logout_user()
    session.clear()

    return redirect(not_authenticated_redirection_url('/'))


@auth_blueprint.post('/refresh-token')
@api_doc(tags=['Auth'], summary='Refresh authentication tokens')
def refresh_token_endpoint():
    """
    Refresh authentication tokens using a valid refresh token

    This is the only endpoint that rotates a session's refresh token, and
    the only one where presenting a stale one is treated as evidence of
    theft (VI-004). The exchange is one-shot: the token handed in stops
    working the instant its replacement is handed out, so a copy lifted
    off the wire is usable at most until the legitimate holder refreshes
    — at which point whichever party arrives second trips reuse detection
    and the whole family is revoked.
    """
    data = request.get_json(silent=True) or {}
    refresh_token = _read_refresh_token(data)
    if not refresh_token:
        return response_api_error('Refresh token is required')

    try:
        # Decode the token manually to check the type
        payload = jwt.decode(refresh_token, app.config.get('SECRET_KEY'), algorithms=['HS256'])

        # Verify it's a refresh token
        if payload.get('type') != 'refresh':
            return response_api_error('Invalid token type')

        # Before anything else, and regardless of the state of the user
        # row: a replayed token has to be caught even if the account it
        # names has since been deactivated.
        session_id = payload.get('sid')
        if not auth_session_accepts_refresh(session_id, payload.get('jti')):
            return response_api_error('Invalid refresh token')

        user_id = payload.get('user_id')
        user = users_get_active(user_id)

        # Carry the MFA verification state across the refresh. Without this,
        # `generate_auth_tokens(user)` would default `mfa_verified=False` and
        # — for users with MFA enrolled — every refresh would issue a token
        # that fails our central MFA enforcement check (logging the user out
        # every 15 minutes). Equally important: a step-1 refresh token
        # (mfa_verified=False) MUST keep producing step-1 access tokens, so
        # a stolen step-1 refresh can't be laundered into a verified token
        # without going through /mfa-verify.
        mfa_verified = bool(payload.get('mfa_verified', False))
        new_tokens = generate_auth_tokens(user, mfa_verified=mfa_verified,
                                          session_id=session_id)

        return response_api_success({'tokens': new_tokens})

    except ObjectNotFoundError:
        return response_api_not_found()
    except BusinessProcessingError:
        # Raised when the family was revoked between the check above and
        # the rotation — a logout or a reuse landing on another worker.
        return response_api_error('Invalid refresh token')
    except jwt.ExpiredSignatureError:
        return response_api_error('Refresh token has expired')
    except jwt.InvalidTokenError:
        return response_api_error('Invalid refresh token')
