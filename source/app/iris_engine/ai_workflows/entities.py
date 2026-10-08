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

"""The objects a run is about: alerts, alert clusters, cases and war
rooms. Access is checked the way the REST API checks it for the user,
without a request."""

from app.business.access_controls import ac_fast_check_user_has_case_access
from app.business.access_controls import ac_fast_check_user_has_cases_access
from app.business.access_controls import access_controls_user_has_customer_access
from app.business.war_rooms_access import ac_fast_check_user_has_war_room_access
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_alert_cluster_summary
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_entity_customer
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_entity_exists
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_entity_row
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_get_user
from app.iris_engine.access_control.utils import ac_get_effective_permissions_of_user
from app.iris_engine.webhooks.payload import webhooks_serialize
from app.models.ai_workflows import ENTITY_ALERT
from app.models.ai_workflows import ENTITY_ALERT_CLUSTER
from app.models.ai_workflows import ENTITY_CASE
from app.models.ai_workflows import ENTITY_WAR_ROOM
from app.models.authorization import CaseAccessLevel
from app.models.authorization import Permissions
from app.models.authorization import WarRoomAccessLevel
from app.models.authorization import ac_flag_match_mask
from app.models.authorization import ac_has_permission_server_administrator

# Snapshot caps: what an agent gets as "the entity" stays prompt-sized
_MAX_STRING = 4000
_MAX_LIST = 50
_MAX_DEPTH = 6


def _permissions(user_id):
    user = ai_workflows_db_get_user(user_id) if user_id else None
    if user is None or not getattr(user, 'active', True):
        return None, 0
    return user, ac_get_effective_permissions_of_user(user)


def ai_workflows_entities_user_can_access(user_id, entity_type, entity_id) -> bool:
    """Whether `user_id` may read the entity. No entity: True (nothing
    to protect); unknown entity type or missing user: False."""
    if not entity_type or entity_id is None:
        return True
    user, permissions = _permissions(user_id)
    if user is None:
        return False
    is_admin = ac_has_permission_server_administrator(permissions)

    if entity_type == ENTITY_CASE:
        return bool(ac_fast_check_user_has_case_access(
            user.id, entity_id, [CaseAccessLevel.read_only, CaseAccessLevel.full_access]))

    if entity_type == ENTITY_WAR_ROOM:
        if not is_admin and not ac_flag_match_mask(permissions, Permissions.war_rooms_read.value):
            return False
        return bool(ac_fast_check_user_has_war_room_access(
            user.id, entity_id, [WarRoomAccessLevel.read_only, WarRoomAccessLevel.full_access]))

    if entity_type in (ENTITY_ALERT, ENTITY_ALERT_CLUSTER):
        needed = Permissions.alerts_read if entity_type == ENTITY_ALERT else Permissions.alert_clusters_read
        if not is_admin and not ac_flag_match_mask(permissions, needed.value):
            return False
        if not ai_workflows_db_entity_exists(entity_type, entity_id):
            return False
        customer_id = ai_workflows_db_entity_customer(entity_type, entity_id)
        return bool(access_controls_user_has_customer_access(user, permissions, customer_id))

    return False


# Permission an API-key scope must keep for the entity to stay readable
# (cases are gated by their ACL alone, as in the REST API)
_SCOPE_PERMISSION = {
    ENTITY_ALERT: Permissions.alerts_read,
    ENTITY_ALERT_CLUSTER: Permissions.alert_clusters_read,
    ENTITY_WAR_ROOM: Permissions.war_rooms_read,
}


def ai_workflows_entities_scope_allows(entity_type, scope_mask) -> bool:
    """Whether an API-key `scope_mask` (None: unrestricted) still lets
    its holder read entities of `entity_type`."""
    if scope_mask is None or not entity_type:
        return True
    if ac_has_permission_server_administrator(int(scope_mask)):
        return True
    needed = _SCOPE_PERMISSION.get(entity_type)
    return needed is None or ac_flag_match_mask(int(scope_mask), needed.value)


def ai_workflows_entities_filter_accessible(user_id, entity_type, entity_ids) -> list:
    """The ids of `entity_ids` (kept in order) `user_id` may read."""
    entity_ids = [i for i in entity_ids or [] if i is not None]
    if not entity_ids:
        return []
    if entity_type == ENTITY_CASE:
        user, _permissions_mask = _permissions(user_id)
        if user is None:
            return []
        granted = ac_fast_check_user_has_cases_access(
            user.id, entity_ids, [CaseAccessLevel.read_only, CaseAccessLevel.full_access])
        return [i for i in entity_ids if i in granted]
    return [i for i in entity_ids if ai_workflows_entities_user_can_access(user_id, entity_type, i)]


def ai_workflows_entities_customer(entity_type, entity_id):
    return ai_workflows_db_entity_customer(entity_type, entity_id)


def _cap(value, depth=0):
    if isinstance(value, str):
        return value if len(value) <= _MAX_STRING else f'{value[:_MAX_STRING]}… [truncated]'
    if depth >= _MAX_DEPTH and isinstance(value, (dict, list)):
        return '[truncated]'
    if isinstance(value, dict):
        return {k: _cap(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        capped = [_cap(v, depth + 1) for v in value[:_MAX_LIST]]
        if len(value) > _MAX_LIST:
            capped.append(f'[{len(value) - _MAX_LIST} more]')
        return capped
    return value


def ai_workflows_entities_snapshot(entity_type, entity_id) -> dict:
    """JSON-safe, size-capped view of the entity; {} when it is gone.
    The caller has checked access."""
    if not entity_type or entity_id is None:
        return {}
    if entity_type == ENTITY_ALERT_CLUSTER:
        data = ai_workflows_db_alert_cluster_summary(entity_id)
    else:
        row = ai_workflows_db_entity_row(entity_type, entity_id)
        data = webhooks_serialize(row) if row is not None else None
    if not isinstance(data, dict):
        return {}
    data = _cap(data)
    data['entity_type'] = entity_type
    data['entity_id'] = entity_id
    return data
