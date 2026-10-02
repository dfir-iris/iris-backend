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

"""Per-case access grants, changed from the case itself.

The `/manage/users|groups/<id>/cases-access` routes do the same writes for
administrators, across any number of cases. These helpers are the
single-case variant behind `/api/v2/cases/<id>/access/*`, which a holder of
`case_access_manage` may use on a case they already have full access to.
Who may call them is decided in the blueprint; this module only validates
the request and performs it.
"""

from app.business.access_controls import access_controls_user_has_customer_access
from app.datamgmt.manage.manage_groups_db import add_case_access_to_group
from app.datamgmt.manage.manage_groups_db import get_group_with_members
from app.datamgmt.manage.manage_groups_db import get_groups_access_to_case
from app.datamgmt.manage.manage_users_db import add_case_access_to_user
from app.datamgmt.manage.manage_users_db import get_user
from app.iris_engine.access_control.utils import ac_get_effective_permissions_of_user
from app.iris_engine.access_control.utils import ac_recompute_effective_ac_from_users_list
from app.iris_engine.utils.tracker import track_activity
from app.models.authorization import CaseAccessLevel
from app.models.authorization import Group
from app.models.authorization import User
from app.models.cases import Cases
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError
from app.util import add_obj_history_entry


def _cases_access_level(access_level) -> CaseAccessLevel:
    # `bool` is an `int`: refuse it rather than read `true` as deny_all.
    if isinstance(access_level, bool) or not isinstance(access_level, int):
        raise BusinessProcessingError('Invalid access level')
    if not CaseAccessLevel.has_value(access_level):
        raise BusinessProcessingError('Invalid access level')
    return CaseAccessLevel(access_level)


def _cases_access_record(case: Cases, change: str):
    add_obj_history_entry(case, change, commit=True)
    track_activity(change, caseid=case.case_id)


def cases_access_get_user(user_identifier) -> User:
    user = get_user(user_identifier)
    if user is None:
        raise ObjectNotFoundError()
    return user


def cases_access_get_group(group_identifier) -> Group:
    """The group, with `group_members` set to its `[{id, user, name}]` members."""
    group = get_group_with_members(group_identifier)
    if group is None:
        raise ObjectNotFoundError()
    return group


def cases_access_user_sees_customer(user_identifier, customer_identifier) -> bool:
    """Whether the user can reach the customer at all — membership, or server administrator."""
    user = get_user(user_identifier)
    if user is None:
        return False
    permissions = ac_get_effective_permissions_of_user(user)
    return access_controls_user_has_customer_access(user, permissions, customer_identifier)


def cases_access_list_groups(case: Cases) -> list[dict]:
    return get_groups_access_to_case(case.case_id)


def cases_access_set_user(case: Cases, user: User, access_level) -> CaseAccessLevel:
    level = _cases_access_level(access_level)

    _, logs = add_case_access_to_user(user, [case.case_id], level.value)
    if logs != 'Updated':
        raise BusinessProcessingError(logs)

    _cases_access_record(case, f'access changed to {level.name} for user {user.name}')
    return level


def cases_access_set_group(case: Cases, group: Group, access_level) -> CaseAccessLevel:
    level = _cases_access_level(access_level)

    # Unlike the admin route, leave `group_auto_follow` alone: a grant on
    # one case says nothing about the group following future cases.
    _, logs = add_case_access_to_group(group, [case.case_id], level.value)
    if logs != 'Updated':
        raise BusinessProcessingError(logs)
    ac_recompute_effective_ac_from_users_list(group.group_members)

    _cases_access_record(case, f'access changed to {level.name} for group {group.group_name}')
    return level
