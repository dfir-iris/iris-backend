#  IRIS Source Code
#  Copyright (C) 2021 - Airbus CyberSecurity (SAS)
#  ir@cyberactionlab.net
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

import base64
import io
import pyotp
import qrcode
import random
import string
import time
from urllib.parse import urlencode
from flask import Blueprint, flash
from flask import redirect
from flask import render_template
from flask import request
from flask import session
from flask import url_for
from oic import rndstr
from oic.oic.message import AuthorizationResponse

from app import app
from app import bc
from app.db import db
from app import oidc_client
from app.blueprints.access_controls import is_authentication_oidc
from app.blueprints.access_controls import is_authentication_ldap
from app.blueprints.responses import response_error
from app.business.auth import safe_next_url
from app.business.auth import validate_ldap_login
from app.business.login_throttle import login_lockout_seconds
from app.business.login_throttle import register_login_failure
from app.business.login_throttle import register_login_success
from app.business.users import retrieve_user_by_username
from app.business.auth import wrap_login_user
from app.datamgmt.manage.manage_users_db import bind_user_external_id
from app.datamgmt.manage.manage_users_db import create_user
from app.datamgmt.manage.manage_users_db import update_user_groups
from app.datamgmt.manage.manage_users_db import get_user
from app.datamgmt.manage.manage_users_db import get_user_by_external_id
from app.forms import LoginForm, MFASetupForm
from app.blueprints.iris_user import iris_current_user
from app.iris_engine.demo_builder import demo_mode_blocks_mfa
from app.iris_engine.utils.tracker import track_activity
from app.datamgmt.manage.manage_groups_db import get_groups_list
from app.business.auth import generate_auth_tokens
from app.schema.marshables import UserSchema

login_blueprint = Blueprint("login", __name__, template_folder="templates")

log = app.logger


# filter User out of database through username


def _render_template_login(form, msg):
    organisation_name = app.config.get("ORGANISATION_NAME")
    login_banner = app.config.get("LOGIN_BANNER_TEXT")
    ptfm_contact = app.config.get("LOGIN_PTFM_CONTACT")
    auth_type = app.config.get("AUTHENTICATION_TYPE")
    local_fallback_enabled = bool(app.config.get("AUTHENTICATION_LOCAL_FALLBACK"))

    return render_template(
        "login.html",
        form=form,
        msg=msg,
        organisation_name=organisation_name,
        login_banner=login_banner,
        ptfm_contact=ptfm_contact,
        auth_type=auth_type,
        local_fallback_enabled=local_fallback_enabled,
    )


def _validate_local_login(username, password):
    user = retrieve_user_by_username(username)
    if not user:
        return None

    if bc.check_password_hash(user.password, password):
        return user

    track_activity(
        f"wrong login password for user '{username}' using local auth",
        ctx_less=True,
        display_in_ui=True,
    )
    return None


def _authenticate_ldap(form, username, password, local_fallback=True):
    try:
        user = validate_ldap_login(username, password, local_fallback)
        if user is None:
            register_login_failure(username)
            return _render_template_login(form, "Wrong credentials. Please try again.")

        register_login_success(username)

        user_data = UserSchema(
            exclude=["user_password", "mfa_secrets", "webauthn_credentials"]
        ).dump(user)

        # Generate auth tokens for API access
        tokens = generate_auth_tokens(user)
        user_data.update({"tokens": tokens})

        return wrap_login_user(user_data)

    except Exception as e:
        log.error(e.__str__())
        return _render_template_login(
            form, "LDAP authentication unavailable. Check server logs"
        )


def _authenticate_password(form, username, password):
    user = retrieve_user_by_username(username)
    if not user or user.is_service_account:
        register_login_failure(username)
        return _render_template_login(form, "Wrong credentials. Please try again.")

    if bc.check_password_hash(user.password, password):
        register_login_success(username)
        return wrap_login_user(user)

    register_login_failure(username)
    track_activity(
        f"wrong login password for user '{username}' using local auth",
        ctx_less=True,
        display_in_ui=True,
    )
    return _render_template_login(form, "Wrong credentials. Please try again.")


def _oidc_destination_from_args(args):
    """Return the sanitised post-login destination carried by `args`, or None.

    The destination travels as `redirect` (and, for URLs built by the
    pre-OIDC pages, as the historic `next`), so both spellings are read.
    Only a path that passed `safe_next_url` survives: an absolute URL or
    a protocol-relative `//host` can never be echoed into a Location
    header. A destination pointing back at the login page is dropped as
    well, so a rejected or repeated login cannot bounce the browser
    between /login and /oidc-login for ever.
    """
    destination = safe_next_url(args.get("redirect") or args.get("next"))
    if destination is None:
        return None

    path = destination.split("?", 1)[0].split("#", 1)[0]
    if path == "/login" or path.startswith("/login/"):
        return None

    return destination


def _oidc_login_page_url(destination):
    """Build the SPA login page URL carrying the post-OIDC destination."""
    login_url = "/login?oidc=1"
    if destination:
        login_url = f"{login_url}&{urlencode({'redirect': destination})}"
    return login_url


# Authenticate user
if app.config.get("AUTHENTICATION_TYPE") in ["local", "ldap", "oidc"]:

    @login_blueprint.route("/login", methods=["GET", "POST"])
    def login():
        if iris_current_user.is_authenticated:
            # If the old Jinja UI is the one receiving the post-OIDC
            # redirect, the SPA-only JWT exchange marker never gets
            # consumed. Drop it here so it can't linger and be
            # redeemed later by a rogue XHR from the same browser.
            session.pop("oidc_authenticated", None)

            destination = _oidc_destination_from_args(request.args)
            if destination:
                return redirect(destination)

            return redirect(url_for("index.index"))

        if (
            is_authentication_oidc()
            and app.config.get("AUTHENTICATION_LOCAL_FALLBACK") is False
        ):
            destination = _oidc_destination_from_args(request.args)
            if destination:
                return redirect(url_for("login.oidc_login", redirect=destination))

            return redirect(url_for("login.oidc_login"))

        form = LoginForm(request.form)

        # check if both http method is POST and form is valid on submit
        if not form.is_submitted() and not form.validate():
            return _render_template_login(form, None)

        # assign form data to variables
        username = request.form.get("username", "", type=str)
        password = request.form.get("password", "", type=str)

        # Checked before the password is verified (VI-010) so a throttled
        # account can't be probed through response content or timing.
        lockout = login_lockout_seconds(username)
        if lockout:
            track_activity(
                f"Throttled login attempt for user '{username}'",
                ctx_less=True,
                display_in_ui=True,
            )
            return _render_template_login(
                form, f"Too many login attempts. Try again in {lockout} seconds."
            )

        if is_authentication_ldap() is True:
            return _authenticate_ldap(
                form,
                username,
                password,
                app.config.get("AUTHENTICATION_LOCAL_FALLBACK"),
            )

        return _authenticate_password(form, username, password)


if is_authentication_oidc():

    @login_blueprint.route("/oidc-login")
    def oidc_login():
        destination = _oidc_destination_from_args(request.args)

        if iris_current_user.is_authenticated:
            if destination:
                return redirect(destination)

            return redirect(url_for("index.index"))

        # The session survives the trip through the provider (the state
        # and nonce below rely on exactly that), so the destination the
        # request asked to come back to is parked here rather than being
        # squeezed into the provider's redirect_uri, which must stay
        # registered verbatim with the IdP.
        if destination:
            session["oidc_next"] = destination
        else:
            session.pop("oidc_next", None)

        session["oidc_state"] = rndstr()
        session["oidc_nonce"] = rndstr()

        xf_proto = request.headers.get("X-Forwarded-Proto")
        xf_host = request.headers.get("X-Forwarded-Host")

        if xf_proto:
            xf_proto = xf_proto.split(",")[0].strip()
        if xf_host:
            xf_host = xf_host.split(",")[0].strip()

        redirect_uri = url_for("login.oidc_authorise", _external=True)

        if xf_proto and xf_host:
            redirect_uri = f"{xf_proto}://{xf_host}/oidc-authorize"

        args = {
            "client_id": oidc_client.client_id,
            "response_type": "code",
            "scope": app.config.get("OIDC_SCOPES"),
            "nonce": session["oidc_nonce"],
            "redirect_uri": redirect_uri,
            "state": session["oidc_state"],
        }

        auth_req = oidc_client.construct_AuthorizationRequest(request_args=args)
        login_url = auth_req.request(oidc_client.authorization_endpoint)

        return redirect(login_url)


# `User.external_id` is a shared namespace — case transfers park imported
# identities there under a prefix of their own. Keeping the provider subjects
# behind `oidc:` stops a crafted subject from ever resolving to one of those
# placeholder accounts, and vice versa.
_OIDC_EXTERNAL_ID_PREFIX = "oidc:"


def _oidc_refuse(message):
    """Turn an OIDC callback away and leave an operator-visible trail.

    Everything routed through here is a token that wanted an account it did not
    prove it owned, so unlike the generic protocol failures above these are
    surfaced in the UI: a burst of them is someone probing the account mapping.
    """
    log.warning(message)
    track_activity(message, ctx_less=True, display_in_ui=True)
    return redirect(url_for("login.login"))


def _oidc_email_claim_is_trusted(claims):
    """Return True when the token's self-asserted profile may name a local account.

    `email_verified` is the only per-token signal a provider gives about whether
    it checked the profile data it just sent, so it is what stands in for "these
    claims were validated". `email_verified: false` is the provider stating the
    address was never proven to belong to whoever holds the token; trusting it
    lets anyone who can sign up at the provider type in the address — or the
    login — of an existing IRIS user and be handed that account (VI-011), so an
    explicit denial is refused whatever the deployment asked for. A provider that
    omits the claim is merely silent rather than negative — that stays allowed by
    default so existing deployments keep working, and operators who want silence
    treated as a denial set OIDC_REQUIRE_VERIFIED_EMAIL.
    """
    email_verified = claims.get("email_verified")

    if email_verified is None:
        return not app.config.get("OIDC_REQUIRE_VERIFIED_EMAIL")

    # Some providers serialise the claim as a string, where "false" is truthy.
    if isinstance(email_verified, str):
        return email_verified.strip().lower() in ("true", "1")

    return bool(email_verified)


def _oidc_external_id_from_claims(claims):
    """Derive the identity to key the local account on, or refuse the token.

    Returns `(external_id, refusal)`; a refusal message means the callback must
    not log anybody in. A token without a `sub` carries nothing durable to bind
    to, and falling back to the name claims for it is precisely the behaviour
    VI-011 reports, so it is turned away rather than resolved by name.
    """
    subject = claims.get("sub")
    if not subject:
        return None, ("OIDC authentication refused: the token carries no 'sub' claim, so there "
                      "is nothing stable to bind a local account to")

    return f"{_OIDC_EXTERNAL_ID_PREFIX}{subject}", None


def _oidc_resolve_user(external_id, user_login, claims_are_verified):
    """Map an authenticated OIDC subject onto a local account.

    Returns `(user, refusal)`. A refusal message means the token must not be
    logged in at all; `(None, None)` means no account matches the subject yet
    and the caller may create one.

    `sub` is the only part of the token the provider guarantees is stable and
    unique for the lifetime of the account. `preferred_username` and `email` are
    routinely user-editable and, on a multi-tenant provider, not even unique.
    Picking the local account from those claims alone (VI-011) meant anyone who
    could rename themselves to `administrator` at the provider got the IRIS
    administrator's session. So the account is keyed on the subject, and the name
    claims only get a say the first time a subject is seen — after that,
    renaming yourself at the provider moves nothing.

    `claims_are_verified` gates that one say: see the adoption branch below.
    """
    user = get_user_by_external_id(external_id)
    if user:
        if user.user != user_login:
            log.info(
                f"OIDC subject bound to user '{user.user}' now presents username "
                f"claim '{user_login}' - keeping the account the subject is bound to"
            )
        return user, None

    user = get_user(user_login, "user")
    if not user:
        return None, None

    # Trust on first use: an unbound account is adopted by the first subject
    # that claims its name, which is how existing local accounts migrate to
    # OIDC. An account already carrying an identity is the collision itself —
    # a second subject asking for it is either a provider that recycled a
    # username or an attacker who picked one.
    if user.external_id:
        return None, (
            f"OIDC authentication refused: local user '{user_login}' is already bound to "
            f"external identity '{user.external_id}', not to '{external_id}'"
        )

    # Adoption is the single moment where a name claim, rather than the binding,
    # decides who the caller is — so it is the moment the provider has to be
    # vouching for what it sent. A profile the token holder typed in themselves
    # is not a licence to take over an account that already exists, whichever
    # claim happens to name it. The caller's create-if-missing branch is
    # deliberately not gated this way: nothing is being taken over when no
    # account matched, so there is no victim.
    if not claims_are_verified:
        return None, (
            f"OIDC authentication refused: local user '{user_login}' exists but the claims "
            f"presented by subject '{external_id}' are not verified by the identity provider, "
            f"so the account cannot be taken over"
        )

    if not bind_user_external_id(user.id, external_id):
        return None, (
            f"OIDC authentication refused: local user '{user_login}' was bound to another "
            f"external identity while '{external_id}' was being authenticated"
        )

    log.info(f"Bound OIDC subject to existing user '{user_login}'")
    return user, None


_OIDC_CLAIM_MISSING = object()


def _oidc_claim(claims, path):
    """Read a claim, following dots into nested objects, or None if absent.

    `realm_access.roles` reaches Keycloak's realm roles. A key that itself
    contains dots (a namespaced claim such as `https://example.com/groups`, or
    a client id under `resource_access`) is tried whole before being split,
    longest first, so both spellings resolve.
    """
    if not path:
        return None

    value = _oidc_claim_walk(claims, path.split("."))
    return None if value is _OIDC_CLAIM_MISSING else value


def _oidc_claim_walk(node, parts):
    if not parts:
        return node

    if not hasattr(node, "get"):
        return _OIDC_CLAIM_MISSING

    for end in range(len(parts), 0, -1):
        child = node.get(".".join(parts[:end]), _OIDC_CLAIM_MISSING)
        if child is _OIDC_CLAIM_MISSING:
            continue

        value = _oidc_claim_walk(child, parts[end:])
        if value is not _OIDC_CLAIM_MISSING:
            return value

    return _OIDC_CLAIM_MISSING


def _oidc_claim_groups(claims, path):
    """The group names a token carries: None if the claim is absent, else a list.

    A single group sent as a bare string is one group, not a sequence of
    characters. Anything that is neither a string nor a list carries no group
    the mapping could recognise, so it reads as an empty list.
    """
    value = _oidc_claim(claims, path)
    if value is None:
        return None

    if isinstance(value, str):
        value = [value]

    if not isinstance(value, (list, tuple)):
        log.warning(f"OIDC group claim '{path}' is a {type(value).__name__}, expected a list of names")
        return []

    return [str(name) for name in value if isinstance(name, (str, int)) and str(name)]


def _oidc_group_ids(group_names, roles_mapping):
    """The IRIS group ids the token's group names map to.

    Without OIDC_MAPPING_ROLES the names are matched against IRIS group names.
    A mapped id whose group was since deleted is skipped with a warning rather
    than failing the login on a foreign key.
    """
    groups = {group.group_id: group.group_name for group in get_groups_list()}
    if roles_mapping:
        name_to_id = roles_mapping
    else:
        name_to_id = {name: group_id for group_id, name in groups.items()}

    group_ids = set()
    for name in group_names:
        group_id = name_to_id.get(name)
        if group_id is None:
            continue

        if group_id not in groups:
            log.warning(f"OIDC_MAPPING_ROLES maps '{name}' to group {group_id}, which does not exist")
            continue

        group_ids.add(group_id)

    return group_ids


if is_authentication_oidc():

    @login_blueprint.route("/oidc-authorize")
    def oidc_authorise():
        auth_resp = oidc_client.parse_response(
            AuthorizationResponse, info=request.args, sformat="dict"
        )

        if auth_resp["state"] != session["oidc_state"]:
            track_activity(
                f"OIDC session state '{auth_resp['state']}' does not match authorization state '{session['oidc_state']}'",
                ctx_less=True,
                display_in_ui=True,
            )
            return redirect(url_for("login.login"))

        args = {
            "code": auth_resp["code"],
        }

        xf_proto = request.headers.get("X-Forwarded-Proto")
        xf_host = request.headers.get("X-Forwarded-Host")

        if xf_proto:
            xf_proto = xf_proto.split(",")[0].strip()
        if xf_host:
            xf_host = xf_host.split(",")[0].strip()

        if xf_proto and xf_host:
            public_base = f"{xf_proto}://{xf_host}"
            args["redirect_uri"] = f"{public_base}/oidc-authorize"

        access_token_resp = oidc_client.do_access_token_request(
            state=auth_resp["state"], request_args=args
        )

        # not all providers set email by default, use preferred_username where it's missing
        # Use the mapping from the configuration or default to email or preferred_username if not set
        email_field = app.config.get("OIDC_MAPPING_EMAIL")
        username_field = app.config.get("OIDC_MAPPING_USERNAME")
        usergroup_field = app.config.get("OIDC_MAPPING_USERGROUP")
        userroles_mapping_field = app.config.get("OIDC_MAPPING_ROLES")
        try:
            if "id_token" in access_token_resp and access_token_resp["id_token"]:
                claims = access_token_resp["id_token"]
            else:
                if "access_token" not in access_token_resp:
                    err = access_token_resp.get("error")
                    desc = access_token_resp.get("error_description")
                    log.error(
                        f"OIDC authentication failed: token response missing access_token (error={err}, desc={desc})"
                    )
                    track_activity(
                        f"OIDC authentication failed: token response missing access_token (error={err}, desc={desc})",
                        ctx_less=True,
                        display_in_ui=False,
                    )
                    return redirect(url_for("login.login"))

                claims = oidc_client.do_user_info_request(
                    state=auth_resp["state"],
                    access_token=access_token_resp["access_token"],
                )

            user_login = _oidc_claim(claims, username_field) or _oidc_claim(claims, email_field)
            user_name = _oidc_claim(claims, email_field) or _oidc_claim(claims, username_field)

            claimed_groups = _oidc_claim_groups(claims, usergroup_field) if usergroup_field else None

            if not user_login:
                log.error(
                    "OIDC authentication failed: username or email not found in id_token"
                )
                track_activity(
                    "OIDC authentication failed: username or email not found in id_token",
                    ctx_less=True,
                    display_in_ui=False,
                )
                return redirect(url_for("login.login"))
        except Exception as e:
            log.error(f"OIDC authentication failed: {str(e)}")
            track_activity(
                f"OIDC authentication failed: {str(e)}",
                ctx_less=True,
                display_in_ui=False,
            )
            return redirect(url_for("login.login"))

        external_id, refusal = _oidc_external_id_from_claims(claims)
        if refusal:
            return _oidc_refuse(refusal)

        user, refusal = _oidc_resolve_user(
            external_id, user_login, _oidc_email_claim_is_trusted(claims)
        )
        if refusal:
            return _oidc_refuse(refusal)

        if not user:
            log.warning(f"OIDC user {user_login} not found in database")
            if app.config.get("AUTHENTICATION_CREATE_USER_IF_NOT_EXIST") is False:
                log.warning("Authentication is set to not create user if not exists")
                track_activity(
                    f"OIDC user {user_login} not found in database",
                    ctx_less=True,
                    display_in_ui=True,
                )
                return response_error("User not found in IRIS", status=404)

            log.info(f"Creating OIDC user {user_login} in database")
            track_activity(
                f"Creating OIDC user {user_login} in database",
                ctx_less=True,
                display_in_ui=False,
            )

            # generate random password
            password = "".join(random.choices(string.printable[:-6], k=16))

            user = create_user(
                user_name,
                user_login,
                bc.generate_password_hash(password.encode("utf8")).decode("utf8"),
                user_login,
                True,
                user_is_service_account=False,
                external_id=external_id,
            )

        if user and not user.active:
            return response_error("User not active in IRIS", status=403)

        # Once a group claim is configured the provider is authoritative for
        # membership: an empty or absent claim means "no groups", so someone
        # removed from every group at the provider loses their IRIS groups too
        # instead of keeping them. Synced before the refusal below so sessions
        # the account already holds lose the old groups' rights as well.
        if usergroup_field:
            update_user_groups(
                user.id, _oidc_group_ids(claimed_groups or [], userroles_mapping_field)
            )

            if claimed_groups is None and userroles_mapping_field:
                return response_error(
                    "Required user group information missing in OIDC response", status=403
                )

        # Mark this session as OIDC-authenticated so the SPA can call
        # /api/v2/auth/oidc-exchange exactly once to trade the session
        # cookie for JWT tokens. Single-use: the exchange endpoint
        # clears both this flag and the session immediately, so a
        # stolen cookie can't be redeemed twice.
        session["oidc_authenticated"] = True

        wrap_login_user(user, is_oidc=True)

        # Regardless of `next` on this callback URL, send the browser to
        # the SPA's login page with an OIDC marker. The SPA's onMount
        # detects the marker, calls oidc-exchange, populates the JWT
        # store, and then navigates back to the destination carried in
        # the `redirect` argument (or to `/` when the request did not ask
        # for one). The destination was sanitised by
        # _oidc_destination_from_args at /login and /oidc-login and is
        # read back from the session here, so an open-redirect via
        # ?next=... on the callback itself is still impossible.
        destination = session.pop("oidc_next", None)
        return redirect(_oidc_login_page_url(destination))


# MFA hardening constants. Values are deliberately conservative — a legitimate
# user mistypes their token occasionally; an attacker brute-forcing 10^6 TOTP
# codes should not be able to linearly grind them. Backport of f596481b.
_MFA_MAX_ATTEMPTS = 5
_MFA_LOCKOUT_SECONDS = 15 * 60


def _get_pre_mfa_user():
    """Return the user this session just passed password auth for, or None.

    The pre_mfa_user_id marker is set exclusively by wrap_login_user after a
    successful password (or LDAP) check. Any MFA handler that runs without it
    is being hit directly by an attacker and must be refused.
    """
    pre_mfa_user_id = session.get("pre_mfa_user_id")
    if pre_mfa_user_id is None:
        return None
    return get_user(pre_mfa_user_id, id_key="id")


def _clear_pre_mfa_state(preserve_lockout=False):
    """Clear the markers that prove this session just passed password auth.

    preserve_lockout: when True, keep the lockout timestamp and fail counter
    so an attacker cannot wipe a brute-force lockout simply by hitting /login
    again.
    """
    session.pop("pre_mfa_user_id", None)
    session.pop("pending_mfa_secret", None)
    if not preserve_lockout:
        session.pop("mfa_fail_count", None)
        session.pop("mfa_lockout_until", None)


def _mfa_is_locked_out():
    locked_until = session.get("mfa_lockout_until")
    if locked_until and locked_until > time.time():
        return True
    if locked_until and locked_until <= time.time():
        session.pop("mfa_lockout_until", None)
        session["mfa_fail_count"] = 0
    return False


def _register_mfa_failure(user, reason):
    session["mfa_fail_count"] = session.get("mfa_fail_count", 0) + 1
    track_activity(
        f"Failed MFA {reason} for user {user.user} "
        f"(attempt {session['mfa_fail_count']}/{_MFA_MAX_ATTEMPTS})",
        ctx_less=True, display_in_ui=True,
    )
    if session["mfa_fail_count"] >= _MFA_MAX_ATTEMPTS:
        session["mfa_lockout_until"] = time.time() + _MFA_LOCKOUT_SECONDS
        # Drop the pending-MFA marker so the attacker has to go back through
        # password auth before they get another burst of attempts. Preserve
        # the lockout timestamp so a fresh /login cannot wipe it.
        _clear_pre_mfa_state(preserve_lockout=True)
        session.pop("username", None)


@app.route("/auth/mfa-setup", methods=["GET", "POST"])
def mfa_setup():
    user = _get_pre_mfa_user()
    if user is None:
        return redirect(url_for("login.login"))

    # Demo mode: every account is shared — the seeded ones publish their
    # credentials on the demo landing page, and anything created during
    # a session is passed around just as freely. Letting one visitor
    # enroll MFA would lock every other visitor out.
    if demo_mode_blocks_mfa():
        flash("MFA is disabled in demo mode.", "danger")
        return redirect(url_for("login.login"))

    # mfa_setup is only for users who haven't completed MFA yet (fresh
    # accounts, or admin-reset accounts where mfa_setup_complete was cleared).
    # A user who already has MFA enrolled must go through mfa_verify — this
    # prevents the "I know the password, let me overwrite the enrolled secret
    # with one I control" bypass.
    if user.mfa_setup_complete and user.mfa_secrets:
        return redirect(url_for("mfa_verify"))

    if _mfa_is_locked_out():
        flash("Too many attempts. Please try again later.", "danger")
        return redirect(url_for("login.login"))

    form = MFASetupForm()

    if form.submit() and form.validate():
        token = form.token.data
        user_password = form.user_password.data
        # The secret MUST come from the server-side session, never from the
        # submitted form. Trusting form.mfa_secret let an attacker enrol a
        # secret of their choosing and immediately log in.
        mfa_secret = session.get("pending_mfa_secret")
        if not mfa_secret:
            flash("MFA setup expired. Please restart the login flow.", "danger")
            return redirect(url_for("login.login"))

        totp = pyotp.TOTP(mfa_secret)

        if totp.verify(token):
            has_valid_password = False
            if is_authentication_ldap() is True:
                if validate_ldap_login(
                    user.user,
                    user_password,
                    local_fallback=app.config.get("AUTHENTICATION_LOCAL_FALLBACK"),
                ):
                    has_valid_password = True

            elif bc.check_password_hash(user.password, user_password):
                has_valid_password = True

            if not has_valid_password:
                _register_mfa_failure(user, "setup (invalid password)")
                flash("Invalid password. Please try again.", "danger")
                return render_template("mfa_setup.html", form=form)

            user.mfa_secrets = mfa_secret
            user.mfa_setup_complete = True
            db.session.commit()
            track_activity(
                f"MFA setup successful for user {user.user}",
                ctx_less=True, display_in_ui=True,
            )

            # Setup succeeded — promote this session to MFA-verified for this
            # user and clear the pre-MFA markers. Without this, wrap_login_user
            # would loop straight back to mfa_verify.
            session["mfa_verified_for_user_id"] = user.id
            _clear_pre_mfa_state()
            return wrap_login_user(user)
        _register_mfa_failure(user, "setup (invalid token)")
        flash("Invalid token or password. Please try again.", "danger")

    # Generate a fresh secret on every GET and stash it in the session. The
    # client only sees the QR code and the base32 key for manual entry; it
    # never sends the secret back.
    temp_otp_secret = pyotp.random_base32()
    session["pending_mfa_secret"] = temp_otp_secret
    otp_uri = pyotp.TOTP(temp_otp_secret).provisioning_uri(
        user.email, issuer_name="IRIS"
    )
    img = qrcode.make(otp_uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    img_str = base64.b64encode(buf.getvalue()).decode()

    return render_template(
        "mfa_setup.html", form=form, img_data=img_str, otp_setup_key=temp_otp_secret
    )


@app.route("/auth/mfa-verify", methods=["GET", "POST"])
def mfa_verify():
    user = _get_pre_mfa_user()
    if user is None:
        return redirect(url_for("login.login"))

    # Redirect user to MFA setup if MFA is not fully set up
    if not user.mfa_secrets or not user.mfa_setup_complete:
        track_activity(
            f"MFA setup required for user {user.user}",
            ctx_less=True, display_in_ui=True,
        )
        return redirect(url_for("mfa_setup"))

    if _mfa_is_locked_out():
        flash("Too many attempts. Please try again later.", "danger")
        return redirect(url_for("login.login"))

    form = MFASetupForm()
    form.user_password.data = "not required for verification"

    if form.submit() and form.validate():
        token = form.token.data
        if not token:
            flash("Token is required.", "danger")
            return render_template("mfa_verify.html", form=form)

        totp = pyotp.TOTP(user.mfa_secrets)
        if totp.verify(token):
            track_activity(
                f"MFA verification successful for user {user.user}",
                ctx_less=True, display_in_ui=True,
            )
            # Bind the MFA-verified marker to this specific user id so that a
            # later login attempt for a different user on the same browser
            # session cannot reuse it.
            session["mfa_verified_for_user_id"] = user.id
            _clear_pre_mfa_state()
            return wrap_login_user(user)
        _register_mfa_failure(user, "verification (invalid token)")
        flash("Invalid token. Please try again.", "danger")

    return render_template("mfa_verify.html", form=form)
