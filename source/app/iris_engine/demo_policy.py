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

"""Demo-mode protection rules, as pure functions.

These decide *what* is off-limits on a demo instance. They deliberately
import nothing: `demo_builder`, their only intended caller, does
`from app import app` — which boots the whole application — so anything
living there cannot be exercised one case at a time. `demo_builder`
keeps the plumbing (reading `app.config`, resolving the current user,
querying accounts) and hands the values down to the functions below.

The rules exist because every visitor of a demo instance holds
`server_administrator`. That permission alone would let one visitor
rename, disable, re-group or delete the very accounts the next visitor
needs to log in with — the landing page publishes their passwords.
"""

# The two groups post-init seeds for a demo instance, and from which
# every demo account inherits its permissions. Editing either one
# rewrites what every visitor can do, so they are frozen the same way
# the seeded accounts are.
DEMO_SEEDED_GROUP_IDS = (1, 2)


# Login of the account post-init bootstraps when the instance has no
# admin yet, used when the deployment did not configure one of its own
# (`IRIS_ADM_USERNAME`). It is the account that owns the instance.
DEFAULT_ADMIN_LOGIN = 'administrator'


def seeded_demo_logins(users_count, adm_count):
    """Logins of the accounts `create_demo_users` seeds.

    Same bounds as `gen_demo_users` / `gen_demo_admins`, which stop at
    `count - 1`, so the list matches exactly what post-init created and
    what the demo landing page publishes.
    """
    logins = [f'user_std_{i}' for i in range(1, int(users_count))]
    logins += [f'adm_{i}' for i in range(1, int(adm_count))]
    return logins


def protected_demo_logins(users_count, adm_count, admin_login=None):
    """Every login a demo instance freezes.

    The seeded accounts plus the instance administrator. The admin
    account is in here — rather than only being covered by the
    owner-id rule below — because it is protected from *everyone*,
    itself included: on a demo instance its password is as good as
    public, and it is the one account whose loss takes the instance
    with it. Its id is not necessarily `DEMO_MODE_OWNER_USER_ID`
    either, if the deployment seeded users before the admin.
    """
    return seeded_demo_logins(users_count, adm_count) + [admin_login or DEFAULT_ADMIN_LOGIN]


def is_protected_demo_user(user_id, user_login, caller_id, owner_user_id, protected_logins):
    """True when the account is off-limits to the caller.

    Two separate reasons, and the caller only matters for the first:

      * whoever holds `owner_user_id` is protected from everyone else —
        that account keeps the demo running, and the rule stands even
        if it was renamed, since it goes by id;
      * a login in `protected_logins` — the seeded accounts and the
        administrator — is protected from *everyone*, the owner
        included. Those passwords are published or shared, so there is
        nobody for whom changing one is safe.

    `caller_id` is None outside a request context (the serialisers ask
    this question too, to flag rows for the UI). An unknown caller is
    not the owner, which is the safe side of the check.
    """
    if caller_id != owner_user_id and user_id == owner_user_id:
        return True

    return user_login in protected_logins


def is_protected_demo_group(group_id, caller_id, owner_user_id):
    """True when the group is off-limits to the caller.

    Same asymmetry as `is_protected_demo_user`: the owner may still
    curate the seeded groups, nobody else may.
    """
    return caller_id != owner_user_id and group_id in DEMO_SEEDED_GROUP_IDS


def coerce_user_ids(user_ids):
    """The ids in `user_ids` as a set of ints, dropping what isn't one.

    Used by the group-membership guard, which is handed a raw member
    list. Anything non-numeric in there is not an account this rule can
    protect, and rejecting the whole request over it belongs to schema
    validation, not to the demo policy — so it is skipped rather than
    raised on.
    """
    ids = set()
    for user_id in user_ids:
        try:
            ids.add(int(user_id))
        except (TypeError, ValueError):
            continue
    return ids


def membership_change(current_ids, submitted_ids):
    """The accounts a membership update actually adds or removes.

    A group's member list is replaced wholesale by the API, so the
    guard must not look at the submitted list alone — re-sending the
    current members unchanged is a no-op and has to keep working even
    when a demo account is among them. Only the symmetric difference
    represents a real permission change.
    """
    return coerce_user_ids(current_ids).symmetric_difference(coerce_user_ids(submitted_ids))
