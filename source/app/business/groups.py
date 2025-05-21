#  IRIS Source Code
#  Copyright (C) 2025 - DFIR-IRIS
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

from app.models.authorization import Group
from app.iris_engine.utils.tracker import track_activity
from app.datamgmt.manage.manage_groups_db import create_group
from app.datamgmt.manage.manage_groups_db import get_group_details
from app.datamgmt.manage.manage_groups_db import update_group
from app.business.errors import ObjectNotFoundError


def groups_create(group: Group) -> Group:
    create_group(group)
    track_activity(f'added group {group.group_name}', ctx_less=True)

    return group


def groups_get(identifier) -> Group:
    group = get_group_details(identifier)
    if not group:
        raise ObjectNotFoundError()
    return group

def groups_update(group_updated: Group) -> Group:
    update_group()
    return group_updated
