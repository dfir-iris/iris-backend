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

import enum
import secrets
import uuid
from flask_login import UserMixin
from sqlalchemy import BigInteger
from sqlalchemy import DateTime
from sqlalchemy import JSON
from sqlalchemy import Boolean
from sqlalchemy import Column
from sqlalchemy import ForeignKey
from sqlalchemy import Integer
from sqlalchemy import LargeBinary
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy import UniqueConstraint
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db import db


class CaseAccessLevel(enum.Enum):
    deny_all = 0x1
    read_only = 0x2
    full_access = 0x4

    @classmethod
    def has_value(cls, value):
        return value in cls._value2member_map_


class Permissions(enum.Enum):
    standard_user = 0x1
    server_administrator = 0x2

    alerts_read = 0x4
    alerts_write = 0x8
    alerts_delete = 0x10

    search_across_cases = 0x20

    customers_read = 0x40
    customers_write = 0x80

    case_templates_read = 0x100
    case_templates_write = 0x200

    activities_read = 0x400
    all_activities_read = 0x800

    custom_dashboards_read = 0x1000
    custom_dashboards_write = 0x2000
    custom_dashboards_share = 0x4000

    war_rooms_read = 0x8000
    war_rooms_write = 0x10000
    war_rooms_create = 0x20000

    alert_clusters_read = 0x40000
    alert_clusters_write = 0x80000
    alert_clusters_delete = 0x100000

    cluster_rules_read = 0x200000
    cluster_rules_write = 0x400000

    investigation_flows_read = 0x800000
    investigation_flows_write = 0x1000000

    asset_manager_read = 0x2000000
    asset_manager_write = 0x4000000


class WarRoomAccessLevel(enum.Enum):
    deny_all = 0x1
    read_only = 0x2
    full_access = 0x4

    @classmethod
    def has_value(cls, value):
        return value in cls._value2member_map_


class Organisation(db.Model):
    __tablename__ = 'organisations'

    org_id = Column(BigInteger, primary_key=True)
    org_uuid = Column(UUID(as_uuid=True), default=uuid.uuid4, nullable=False,
                      server_default=text('gen_random_uuid()'), unique=True)
    org_name = Column(Text, nullable=False, unique=True)
    org_description = Column(Text)
    org_url = Column(Text)
    org_logo = Column(Text)
    org_email = Column(Text)
    org_nationality = Column(Text)
    org_sector = Column(Text)
    org_type = Column(Text)

    UniqueConstraint('org_name')


class OrganisationCaseAccess(db.Model):
    __tablename__ = "organisation_case_access"

    id = Column(BigInteger, primary_key=True)
    org_id = Column(BigInteger, ForeignKey('organisations.org_id'), nullable=False)
    case_id = Column(BigInteger, ForeignKey('cases.case_id'), nullable=False)
    access_level = Column(BigInteger, nullable=False)

    org = relationship('Organisation')
    case = relationship('Cases')

    UniqueConstraint('case_id', 'org_id')


class Group(db.Model):
    __tablename__ = 'groups'

    group_id = Column(BigInteger, primary_key=True)
    group_uuid = Column(UUID(as_uuid=True), default=uuid.uuid4, nullable=False,
                        server_default=text('gen_random_uuid()'), unique=True)
    group_name = Column(Text, nullable=False, unique=True)
    group_description = Column(Text)
    group_permissions = Column(BigInteger, nullable=False)
    group_auto_follow = Column(Boolean, nullable=False, default=False)
    group_auto_follow_access_level = Column(BigInteger, nullable=False, default=0)

    UniqueConstraint('group_name')


class GroupCaseAccess(db.Model):
    __tablename__ = "group_case_access"

    id = Column(BigInteger, primary_key=True)
    group_id = Column(BigInteger, ForeignKey('groups.group_id'), nullable=False)
    case_id = Column(BigInteger, ForeignKey('cases.case_id'), nullable=False)
    access_level = Column(BigInteger, nullable=False)

    group = relationship('Group')
    case = relationship('Cases')

    UniqueConstraint('case_id', 'group_id')


class UserCaseAccess(db.Model):
    __tablename__ = "user_case_access"

    id = Column(BigInteger, primary_key=True, nullable=False)
    user_id = Column(BigInteger, ForeignKey('user.id'), nullable=False)
    case_id = Column(BigInteger, ForeignKey('cases.case_id'), nullable=False)
    access_level = Column(BigInteger, nullable=False)

    user = relationship('User')
    case = relationship('Cases')

    UniqueConstraint('case_id', 'user_id')


class UserCaseEffectiveAccess(db.Model):
    __tablename__ = "user_case_effective_access"

    id = Column(BigInteger, primary_key=True, nullable=False)
    user_id = Column(BigInteger, ForeignKey('user.id'), nullable=False)
    case_id = Column(BigInteger, ForeignKey('cases.case_id'), nullable=False)
    access_level = Column(BigInteger, nullable=False)

    user = relationship('User')
    case = relationship('Cases')

    UniqueConstraint('case_id', 'user_id')


class UserOrganisation(db.Model):
    __tablename__ = "user_organisation"

    id = Column(BigInteger, primary_key=True, nullable=False)
    user_id = Column(BigInteger, ForeignKey('user.id'), nullable=False)
    org_id = Column(BigInteger, ForeignKey('organisations.org_id'), nullable=False)
    is_primary_org = Column(Boolean, nullable=False)

    user = relationship('User')
    org = relationship('Organisation')

    UniqueConstraint('user_id', 'org_id')


class UserGroup(db.Model):
    __tablename__ = "user_group"

    id = Column(BigInteger, primary_key=True, nullable=False)
    user_id = Column(BigInteger, ForeignKey('user.id'), nullable=False)
    group_id = Column(BigInteger, ForeignKey('groups.group_id'), nullable=False)

    user = relationship('User')
    group = relationship('Group')

    UniqueConstraint('user_id', 'group_id')


class UserClient(db.Model):
    __tablename__ = "user_client"

    id = Column(BigInteger, primary_key=True, nullable=False)
    user_id = Column(BigInteger, ForeignKey('user.id'), nullable=False)
    client_id = Column(BigInteger, ForeignKey('client.client_id'), nullable=False)
    access_level = Column(BigInteger, nullable=False)
    allow_alerts = Column(Boolean, nullable=False)

    user = relationship('User')
    client = relationship('Client')

    UniqueConstraint('user_id', 'client_id')


class User(UserMixin, db.Model):
    __tablename__ = 'user'

    id = Column(BigInteger, primary_key=True)
    user = Column(String(64), unique=True)
    name = Column(String(64), unique=False)
    email = Column(String(120), unique=True)
    uuid = Column(UUID(as_uuid=True), default=uuid.uuid4, nullable=False,
                  server_default=text('gen_random_uuid()'), unique=True)
    password = Column(String(500))
    ctx_case = Column(Integer)

    # TODO this colum could be removed: the case name is now retrieved from the ctx_case (case identifier)
    # DO NOT ACCESS this column anymore
    ctx_human_case = Column(String(256))
    active = Column(Boolean())
    api_key = Column(Text(), unique=True)
    external_id = Column(Text, unique=True)
    in_dark_mode = Column(Boolean())
    has_mini_sidebar = Column(Boolean(), default=False)
    has_deletion_confirmation = Column(Boolean(), default=False)
    is_service_account = Column(Boolean(), default=False)
    mfa_secrets = Column(Text, nullable=True)
    webauthn_credentials = Column(JSON, nullable=True)
    mfa_setup_complete = Column(Boolean(), default=False)
    # Avatar storage. `avatar_blob` is the normalised 256x256 PNG
    # served by `/api/v2/users/<id>/avatar`; `avatar_mime` is the
    # MIME used for the Content-Type header on that response;
    # `avatar_updated_at` powers both the ETag and `Last-Modified`
    # so clients revalidate cheaply.
    avatar_blob = Column(LargeBinary, nullable=True)
    avatar_mime = Column(String(64), nullable=True)
    avatar_updated_at = Column(DateTime, nullable=True)

    # Free-form per-user preferences bag. Keyed by feature namespace
    # (e.g. `war_room_stream` for the chat sidebar filter selection)
    # so we can add more preferences later without new migrations.
    # See migration d8e3f1a90c17.
    preferences = Column(JSONB, nullable=True)

    # Per-user MCP opt-out. When False, all MCP tool/resource calls
    # authenticated as this user are denied even if `ServerSettings.
    # mcp_enabled` is True. Lets an admin cut MCP off for individual
    # high-privilege accounts without disabling MCP globally.
    mcp_allowed = Column(Boolean, nullable=False, default=True,
                         server_default=text('true'))

    groups = relationship('Group', secondary='user_group', viewonly=True)
    permissions = relationship('Group', secondary='user_group', viewonly=True)
    customers = relationship('Client', secondary='user_client', viewonly=True)

    def __init__(self, user: str, name: str, email: str, password: str, active: bool,
                 external_id: str = None, is_service_account: bool = False, mfa_secret: str = None,
                 webauthn_credentials: list = None, in_dark_mode: bool = False):
        self.user = user
        self.name = name
        self.password = password
        self.email = email
        self.active = active
        self.external_id = external_id
        self.is_service_account = is_service_account
        self.mfa_secrets = mfa_secret
        self.mfa_setup_complete = False
        self.webauthn_credentials = webauthn_credentials or []
        self.in_dark_mode = in_dark_mode

    def __repr__(self):
        return str(self.id) + ' - ' + str(self.user)

    def save(self):

        self.api_key = secrets.token_urlsafe(nbytes=64)

        # inject self into db session
        db.session.add(self)
        db.session.commit()

        return self


class UserApiKey(db.Model):
    """A named, revocable, scope-restricted API key issued to a user.

    Layered on top of the legacy `User.api_key` column: `User.api_key`
    stays populated as a compatibility mirror (one release only) so any
    external automation using the pre-existing single key keeps working,
    while every request routed through `_get_user_by_api_key` also picks
    up per-key metadata — most importantly `scope_mask`, which is
    AND-ed into the caller's effective permission bitmask so a single
    user can hand out keys with narrower reach than their session
    permissions (e.g. a read-only Claude Desktop key issued by an admin
    account).

    `key_hash` stores SHA-256(key) rather than the raw key so a DB dump
    doesn't leak usable credentials. Lookup on ingress is
    `filter_by(key_hash=sha256(header_value))`. Renewal replaces the
    hash — the plaintext is only visible in the 201 response body.

    `scope_mask=None` means "inherit the user's full effective
    permissions" (matches legacy behaviour). A concrete mask AND-s with
    the user's effective mask so an issued key can never expand
    permissions past the user's own.
    """
    __tablename__ = 'user_api_key'

    id = Column(BigInteger, primary_key=True)
    user_id = Column(BigInteger,
                     ForeignKey('user.id', ondelete='CASCADE'),
                     nullable=False)
    # Human-facing label so a user can tell their keys apart on the
    # profile page. Unique per-user; keeps 'Claude Desktop' /
    # 'CI script' etc. from colliding.
    name = Column(String(120), nullable=False)
    # SHA-256 hex digest of the raw key. 64 chars, no padding.
    key_hash = Column(String(64), unique=True, nullable=False, index=True)
    # None → inherit user permissions. Otherwise AND with user's mask
    # inside `_get_current_permissions_mask` when this key is the auth.
    scope_mask = Column(BigInteger, nullable=True)
    created_at = Column(DateTime, nullable=False,
                        server_default=text('now()'))
    # Bumped on every successful auth via this key; NULL until first
    # use. Not indexed — writes are on the hot path, reads are per-user
    # admin queries only.
    last_used_at = Column(DateTime, nullable=True)
    # Non-null → this key is revoked, `_get_user_by_api_key` returns
    # None. We keep the row for audit rather than deleting.
    revoked_at = Column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint('user_id', 'name', name='uq_user_api_key_user_name'),
    )

    user = relationship('User', backref='api_keys')


class UserAuthSession(db.Model):
    """The server-side half of a JWT token family (VI-004).

    Access and refresh tokens are self-contained, which used to mean
    there was nothing to revoke: a refresh token lifted off the wire
    stayed usable for its full 14-day life, side by side with the
    victim's own, and logging out invalidated precisely nothing. This
    row is the handle that makes rotation and revocation possible.

    `sid` is minted once per login and copied into every token of the
    family. `refresh_jti` names the *one* refresh token the family
    currently accepts — the refresh endpoint overwrites it on every
    rotation, so the token that was just spent stops working the moment
    its successor is handed out. A request presenting one of those
    rotated-out ids is therefore a replay of a credential that was
    already exchanged, and the answer is `revoked_at`: the whole family
    dies, because we cannot tell the thief from the victim and only one
    of them should keep the session.

    Rows survive revocation rather than being deleted. `revoked_at` is
    what the per-request access-token gate reads, and the timestamps are
    the only record of when a session opened and last rotated.
    """
    __tablename__ = 'user_auth_session'

    id = Column(BigInteger, primary_key=True)
    # Unique + indexed because the access-token gate looks a session up
    # by `sid` on every authenticated request; that has to stay a single
    # index probe.
    sid = Column(String(36), unique=True, nullable=False, index=True)
    user_id = Column(BigInteger, ForeignKey('user.id', ondelete='CASCADE'),
                     nullable=False, index=True)
    refresh_jti = Column(String(36), nullable=False)
    created_at = Column(DateTime, nullable=False, server_default=text('now()'))
    # Bumped on rotation only — never on plain access-token validation,
    # which must not turn a read into a write on the hot path.
    last_used_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)
    # Whether this family was opened by an OIDC exchange. OIDC users are
    # exempt from IRIS's own MFA — the IdP owns the second factor, which
    # is why `wrap_login_user` takes `is_oidc` and the token exchange
    # mints `mfa_verified=True`. That exemption was call-site knowledge
    # only: nothing recorded it anywhere the refresh path could read, so
    # a refresh could not tell an OIDC session apart from a local one
    # that had simply never been challenged. It lives here rather than in
    # a token claim because the refresh path already loads this row, and
    # because a "skip MFA" flag next to `mfa_verified` in a bearer token
    # is one more thing a leaked signing key would forge. Do not key this
    # off `User.external_id` — the case-transfer resolvers populate that
    # too, with a placeholder prefix.
    is_oidc = Column(Boolean, nullable=False, server_default=text('false'))

    user = relationship('User')


class AuthThrottle(db.Model):
    """Shared failure counter behind both authentication throttles.

    The password throttle and the MFA-verify throttle each kept their
    counters in a module-level dict, which is per OS process. The app runs
    under `gunicorn -w 4`, so an attacker got four independent budgets and
    a container restart handed all four back. A 5-attempt TOTP lockout was
    in practice a 20-attempt one.

    One row per bucket, keyed by an opaque string rather than a user id:
    the MFA throttle counts per user, but the password throttle also
    counts per source address (`client::<addr>`), and an address has no
    user row to point at. Keying on `user_id` would have fitted one caller
    and forced the next one to invent a second table.

    Rows are not swept. A row whose `window_start` and `locked_until` are
    both in the past is inert — every reader compares timestamps rather
    than trusting presence — and the population is bounded by the distinct
    usernames and addresses that have been tried, which is far less than
    the one `UserActivity` row per rejected attempt already being written.
    """
    __tablename__ = 'auth_throttle'

    # Opaque bucket key: `account::<username>`, `client::<address>`,
    # `mfa::<user id>`. Text rather than a bounded String because a
    # username is user-supplied and truncating one would silently merge
    # two accounts into a single budget.
    key = Column(Text, primary_key=True)
    failures = Column(Integer, nullable=False, server_default=text('0'))
    window_start = Column(DateTime, nullable=False, server_default=text('now()'))
    locked_until = Column(DateTime, nullable=True)


class UserFollowedCase(db.Model):
    __tablename__ = 'user_followed_case'
    __table_args__ = (
        UniqueConstraint('user_id', 'case_id', name='uq_user_followed_case_user_case'),
    )

    user_id = Column(BigInteger, ForeignKey('user.id', ondelete='CASCADE'),
                     primary_key=True, nullable=False)
    case_id = Column(BigInteger, ForeignKey('cases.case_id', ondelete='CASCADE'),
                     primary_key=True, nullable=False)
    created_at = Column(DateTime, nullable=False, server_default=text("now()"))

    user = relationship('User')


class UserWarRoomAccess(db.Model):
    __tablename__ = "user_war_room_access"
    __table_args__ = (
        UniqueConstraint('war_room_id', 'user_id', name='uq_user_war_room_access_room_user'),
    )

    id = Column(BigInteger, primary_key=True, nullable=False)
    user_id = Column(BigInteger, ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    war_room_id = Column(BigInteger, ForeignKey('war_room.war_room_id', ondelete='CASCADE'),
                         nullable=False)
    access_level = Column(BigInteger, nullable=False)

    user = relationship('User')


class GroupWarRoomAccess(db.Model):
    __tablename__ = "group_war_room_access"
    __table_args__ = (
        UniqueConstraint('war_room_id', 'group_id', name='uq_group_war_room_access_room_group'),
    )

    id = Column(BigInteger, primary_key=True, nullable=False)
    group_id = Column(BigInteger, ForeignKey('groups.group_id', ondelete='CASCADE'), nullable=False)
    war_room_id = Column(BigInteger, ForeignKey('war_room.war_room_id', ondelete='CASCADE'),
                         nullable=False)
    access_level = Column(BigInteger, nullable=False)

    group = relationship('Group')


class UserWarRoomEffectiveAccess(db.Model):
    """Cached effective access per (user, war_room).

    Mirrors `UserCaseEffectiveAccess` so list-queries can be answered
    with a single indexed lookup instead of recomputing the
    user→group→war_room precedence chain on every request.
    """
    __tablename__ = "user_war_room_effective_access"
    __table_args__ = (
        UniqueConstraint('war_room_id', 'user_id',
                         name='uq_user_war_room_effective_access_room_user'),
    )

    id = Column(BigInteger, primary_key=True, nullable=False)
    user_id = Column(BigInteger, ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    war_room_id = Column(BigInteger, ForeignKey('war_room.war_room_id', ondelete='CASCADE'),
                         nullable=False)
    access_level = Column(BigInteger, nullable=False)

    user = relationship('User')


def ac_flag_match_mask(flag, mask):
    return (flag & mask) == mask


def ac_has_permission_server_administrator(permissions):
    return ac_flag_match_mask(permissions, Permissions.server_administrator.value)


def ac_access_level_mask_from_val_list(access_levels) -> int:
    """
    Return an access level mask from a list of access levels
    """
    am = 0
    for acc in access_levels:
        am |= int(acc)

    return am
