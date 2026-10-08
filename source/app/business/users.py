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

import hashlib
import re
import secrets
from datetime import datetime
from zoneinfo import ZoneInfo
from zoneinfo import ZoneInfoNotFoundError

from app.db import db
from app.models.authorization import User
from app.models.authorization import UserApiKey
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError
from app.datamgmt.manage.manage_users_db import get_active_user
from app.datamgmt.manage.manage_users_db import get_user_details_return_user
from app.datamgmt.manage.manage_users_db import get_active_user_by_login
from app.datamgmt.manage.manage_users_db import create_user
from app.datamgmt.manage.manage_users_db import update_user
from app.datamgmt.manage.manage_users_db import delete_user
from app.datamgmt.manage.manage_users_db import get_user_organisations
from app.datamgmt.manage.manage_users_db import get_user_primary_org
from app.datamgmt.manage.manage_users_db import get_users_list_restricted_from_case as get_users_list_restricted_from_case
from app.datamgmt.manage.manage_users_db import get_user_cases_fast as get_user_cases_fast
from app.datamgmt.manage.manage_users_db import add_case_access_to_user as add_case_access_to_user
from app.datamgmt.manage.manage_users_db import get_user as get_user
from app.datamgmt.manage.manage_users_db import get_user_details as get_user_details
from app.datamgmt.manage.manage_users_db import remove_cases_access_from_user as remove_cases_access_from_user
from app.datamgmt.manage.manage_users_db import update_user_customers as update_user_customers
from app.datamgmt.manage.manage_users_db import update_user_groups as update_user_groups
from app.datamgmt.comments import user_has_comments
from app.datamgmt.ai_workflows.ai_workflows_business_db import ai_workflows_business_db_count_owned
from app.iris_engine.utils.tracker import track_activity

# Display timezone preference. `browser` defers to the timezone of
# whatever browser the user is on; anything else is an IANA name
# (`UTC`, `Europe/Paris`, ...). Stored under this key in the
# `User.preferences` JSONB bag.
USERS_TIMEZONE_BROWSER = 'browser'
_USERS_TIMEZONE_PREFERENCE_KEY = 'timezone'

# Clock preference: `24h` (the default), `12h`, or `locale` to let the
# browser locale decide. Stored under this key in `User.preferences`.
USERS_TIME_FORMAT_DEFAULT = '24h'
_USERS_TIME_FORMATS = ('24h', '12h', 'locale')
_USERS_TIME_FORMAT_PREFERENCE_KEY = 'time_format'

# War-room section tabs, in the order the user arranged them. The SPA owns
# the set of tab keys (it drops unknown ones and appends missing ones), so
# only the shape is checked here. An empty list means the default order.
# Stored under this key in `User.preferences`.
_USERS_WAR_ROOM_TAB_ORDER_PREFERENCE_KEY = 'war_room_tab_order'
_USERS_WAR_ROOM_TAB_ORDER_MAX_TABS = 32
_USERS_WAR_ROOM_TAB_KEY_PATTERN = re.compile(r'[a-z][a-z0-9_-]{0,31}')


def users_reset_mfa(user_id: int = None):
    """
    Resets a user MFA by setting to none its MFA token
    """
    user = get_active_user(user_id=user_id)
    if user is None:
        raise BusinessProcessingError(f'User with id {user_id} is not found')

    user.mfa_secrets = None
    user.mfa_setup_complete = False

    db.session.commit()


def retrieve_user_by_username(username: str):
    """
    Retrieve the user object by username.

    :param username: Username
    :return: User object if found, None
    """
    user = get_active_user_by_login(username)
    if not user:
        track_activity(f'someone tried to log in with user "{username}", which does not exist',
                       ctx_less=True, display_in_ui=True)
    return user


def users_create(user: User, active) -> User:
    user = create_user(user.name,
                       user.user,
                       user.password,
                       user.email,
                       active,
                       user_is_service_account=user.is_service_account)

    track_activity(f'created user {user.user}', ctx_less=True)
    return user


def users_get(identifier) -> User:
    user = get_user_details_return_user(identifier)
    if not user:
        raise ObjectNotFoundError()
    return user


def users_get_active(user_id) -> User:
    user = get_active_user(user_id)
    if not user:
        raise ObjectNotFoundError
    return user


def users_update(user: User, user_password: str) -> User:
    user = update_user(user, password=user_password)
    track_activity(f'updated user {user.user}', ctx_less=True)
    return user


def users_delete(user: User):
    if user.active:
        raise BusinessProcessingError('Cannot delete active user')
    if user_has_comments(user):
        raise BusinessProcessingError('Cannot delete user with associated comments')
    owned_workflows = ai_workflows_business_db_count_owned(user.id)
    if owned_workflows:
        raise BusinessProcessingError(f'User owns {owned_workflows} AI workflows; transfer ownership first')
    delete_user(user.id)
    track_activity(message=f'deleted user ID {user.id}', ctx_less=True)


def users_is_valid_timezone(value) -> bool:
    if value == USERS_TIMEZONE_BROWSER:
        return True
    if not isinstance(value, str) or not value or len(value) > 64:
        return False
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def users_get_timezone(user: User) -> str:
    """Return the user's display timezone preference.

    The generic `/manage/users/me/preferences/<key>` route stores values
    verbatim, so the stored value is re-validated here rather than
    trusted: anything unusable falls back to the browser timezone.
    """
    value = (user.preferences or {}).get(_USERS_TIMEZONE_PREFERENCE_KEY)
    if value is not None and users_is_valid_timezone(value):
        return value
    return USERS_TIMEZONE_BROWSER


def users_set_timezone(user: User, value: str):
    if not users_is_valid_timezone(value):
        raise BusinessProcessingError('timezone must be "browser" or a valid IANA timezone name')

    # Copy-and-replace: JSONB is not deep-tracked, an in-place mutation
    # would not be flushed.
    preferences = dict(user.preferences or {})
    if value == USERS_TIMEZONE_BROWSER:
        preferences.pop(_USERS_TIMEZONE_PREFERENCE_KEY, None)
    else:
        preferences[_USERS_TIMEZONE_PREFERENCE_KEY] = value
    user.preferences = preferences


def users_get_time_format(user: User) -> str:
    """Return the user's clock preference, re-validated like the timezone."""
    value = (user.preferences or {}).get(_USERS_TIME_FORMAT_PREFERENCE_KEY)
    if value in _USERS_TIME_FORMATS:
        return value
    return USERS_TIME_FORMAT_DEFAULT


def users_set_time_format(user: User, value: str):
    if value not in _USERS_TIME_FORMATS:
        raise BusinessProcessingError(f'time_format must be one of {", ".join(_USERS_TIME_FORMATS)}')

    preferences = dict(user.preferences or {})
    if value == USERS_TIME_FORMAT_DEFAULT:
        preferences.pop(_USERS_TIME_FORMAT_PREFERENCE_KEY, None)
    else:
        preferences[_USERS_TIME_FORMAT_PREFERENCE_KEY] = value
    user.preferences = preferences


def users_is_valid_war_room_tab_order(value) -> bool:
    if not isinstance(value, list) or len(value) > _USERS_WAR_ROOM_TAB_ORDER_MAX_TABS:
        return False
    if not all(isinstance(key, str) and _USERS_WAR_ROOM_TAB_KEY_PATTERN.fullmatch(key) for key in value):
        return False
    return len(set(value)) == len(value)


def users_get_war_room_tab_order(user: User) -> list[str]:
    """Return the user's war-room tab order, re-validated like the timezone."""
    value = (user.preferences or {}).get(_USERS_WAR_ROOM_TAB_ORDER_PREFERENCE_KEY)
    if value is not None and users_is_valid_war_room_tab_order(value):
        return list(value)
    return []


def users_set_war_room_tab_order(user: User, value: list[str]):
    if not users_is_valid_war_room_tab_order(value):
        raise BusinessProcessingError(
            f'war_room_tab_order must be a list of at most {_USERS_WAR_ROOM_TAB_ORDER_MAX_TABS} distinct tab keys')

    preferences = dict(user.preferences or {})
    if value:
        preferences[_USERS_WAR_ROOM_TAB_ORDER_PREFERENCE_KEY] = list(value)
    else:
        preferences.pop(_USERS_WAR_ROOM_TAB_ORDER_PREFERENCE_KEY, None)
    user.preferences = preferences


def api_keys_list(user: User) -> list[UserApiKey]:
    """Return every API key belonging to `user` (revoked included).

    Callers should hide `revoked_at IS NOT NULL` rows from the UI by
    default — see the profile page's `showRevoked` toggle — but the
    list is exhaustive so admins can audit past keys.
    """
    return list(
        UserApiKey.query.filter_by(user_id=user.id)
        .order_by(UserApiKey.created_at.desc())
        .all()
    )


def api_keys_create(user: User, name: str, scope_mask: int | None) -> tuple[UserApiKey, str]:
    """Mint a new API key for `user`. Returns (row, plaintext_key).

    The plaintext is only handed back once, in the create-endpoint's
    response body; the DB stores only `sha256(plaintext_key)`. `name`
    must be unique per-user (enforced by the DB constraint too).
    """
    name = (name or '').strip()
    if not name:
        raise BusinessProcessingError('name is required')
    existing = UserApiKey.query.filter_by(user_id=user.id, name=name).first()
    if existing is not None:
        raise BusinessProcessingError(
            f'An API key named {name!r} already exists for this user.'
        )
    raw = secrets.token_urlsafe(nbytes=64)
    key_hash = hashlib.sha256(raw.encode('utf-8')).hexdigest()
    row = UserApiKey(
        user_id=user.id,
        name=name,
        key_hash=key_hash,
        scope_mask=int(scope_mask) if scope_mask is not None else None,
    )
    db.session.add(row)
    db.session.commit()
    return row, raw


def api_keys_revoke(user: User, key_id: int) -> UserApiKey:
    """Mark a key as revoked. Idempotent — revoking an already-revoked
    key just returns the row unchanged. Deletes are avoided so the
    audit trail on `revoked_at` is preserved."""
    row = UserApiKey.query.filter_by(id=key_id, user_id=user.id).first()
    if row is None:
        raise ObjectNotFoundError()
    if row.revoked_at is None:
        row.revoked_at = datetime.utcnow()
        db.session.commit()
    return row


def get_primary_organisation(user_id):
    uoe = get_user_primary_org(user_id)
    if not uoe:
        return 0
    return uoe.org_id


def get_organisations(user_id):
    return get_user_organisations(user_id)
