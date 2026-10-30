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

"""Who may follow the live changes of a case graph (Socket.IO room
`case_graph_<case_id>`, see `app.iris_engine.case_graph_live`)."""

from app.business.access_controls import ac_fast_check_user_has_case_access
from app.business.cases import cases_exists
from app.datamgmt.manage.manage_users_db import get_user
from app.iris_engine.case_graph_live import case_graph_live_room
from app.models.authorization import CaseAccessLevel
from app.models.errors import ObjectNotFoundError


def case_graph_live_join_room(user_id, case_id) -> str:
    """The room of a case the user may read, as the REST reads check it;
    raises ObjectNotFoundError otherwise."""
    if isinstance(case_id, bool) or not isinstance(case_id, int) or case_id <= 0:
        raise ObjectNotFoundError()
    user = get_user(user_id) if user_id else None
    if user is None or not user.active or not cases_exists(case_id):
        raise ObjectNotFoundError()
    if not ac_fast_check_user_has_case_access(user_id, case_id,
                                              [CaseAccessLevel.read_only, CaseAccessLevel.full_access]):
        raise ObjectNotFoundError()
    return case_graph_live_room(case_id)


def case_graph_live_presence_user(user_id) -> dict:
    """Public identity relayed with a `case-graph-presence` message (set
    by the server, never taken from the client)."""
    user = get_user(user_id) if user_id else None
    if user is None:
        raise ObjectNotFoundError()
    return {'id': user.id, 'name': user.name, 'login': user.user}
