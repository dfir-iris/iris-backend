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

"""Live refresh of the investigation graph of a case, over Socket.IO.

After every committed change of the graph (stored edge, free node, view)
or of the case objects it is built from (asset, IOC, event), the server
sends `case-graph-changed {case_id, entity, action, key?, by_user_id}`
to the room `case_graph_<case_id>`, in the `/notifications` namespace
(the authenticated one the SPA keeps open) and in the default one.
Joining the room checks read access to the case
(`case_graph_live_join_room`). A push carries no data: the clients
re-read the graph over REST."""

import logging


logger = logging.getLogger(__name__)

CASE_GRAPH_LIVE_EVENT = 'case-graph-changed'
CASE_GRAPH_PRESENCE_EVENT = 'case-graph-presence'

CASE_GRAPH_LIVE_NAMESPACES = ('/notifications', '/')

_ENTITIES = frozenset({'edge', 'node', 'view', 'case_object'})
_ACTIONS = frozenset({'created', 'updated', 'deleted'})


def case_graph_live_room(case_id) -> str:
    return f'case_graph_{int(case_id)}'


def _current_user_id():
    """Id of the user of the current request, None outside of one."""
    try:
        from app.blueprints.iris_user import iris_current_user
        return iris_current_user.id
    except Exception:
        return None


def case_graph_live_emit(case_id, entity, action, key=None, by_user_id=None):
    """Tell the open graphs of a case that it changed; never raises (the
    write already succeeded)."""
    try:
        if entity not in _ENTITIES or action not in _ACTIONS:
            raise ValueError(f'Unknown case graph change {entity!r}/{action!r}')
        if by_user_id is None:
            by_user_id = _current_user_id()
        payload = {'case_id': int(case_id), 'entity': entity, 'action': action,
                   'by_user_id': int(by_user_id) if by_user_id else None}
        if key:
            payload['key'] = key
        from app import socket_io
        for namespace in CASE_GRAPH_LIVE_NAMESPACES:
            socket_io.emit(CASE_GRAPH_LIVE_EVENT, payload, room=case_graph_live_room(case_id),
                           namespace=namespace)
    except Exception:
        logger.exception('Could not push a case graph change')
