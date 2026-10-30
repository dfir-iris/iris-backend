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

"""War-room graph (§4.10 of the investigation graph): the graphs of the
attached cases the caller can read, the other attached cases as bare
`case:<id>` nodes (same access model as the board)."""

from flask import Blueprint
from flask import request

from app.blueprints.access_controls import ac_api_requires
from app.blueprints.rest.api_doc import api_doc
from app.blueprints.rest.endpoints import response_api_error
from app.blueprints.rest.endpoints import response_api_success
from app.blueprints.rest.v2.war_rooms.access import require_war_room_read
from app.blueprints.rest.v2.war_rooms.access import war_room_readable_attached_case_ids
from app.business.case_graph_assembly import case_graph_parse_include
from app.business.war_room_graph import war_room_graph_get
from app.business.war_room_scope import war_room_scope_attached_case_ids
from app.models.errors import BusinessProcessingError


war_rooms_graph_blueprint = Blueprint('war_rooms_graph_rest_v2', __name__, url_prefix='/<int:war_room_id>')


@war_rooms_graph_blueprint.get('/graph')
@ac_api_requires()
@api_doc(tags=['WarRoomGraph'],
         summary='Investigation graph of the attached cases (keys prefixed c<case_id>/, same_as across cases)',
         query_params=[('include', str, 'Comma list among events, evidences, tasks, notes, alerts'),
                       ('review_state', str, 'accepted (default) or all')])
def get_war_room_graph(war_room_id):
    err = require_war_room_read(war_room_id)
    if err is not None:
        return err
    try:
        graph = war_room_graph_get(war_room_scope_attached_case_ids(war_room_id),
                                   war_room_readable_attached_case_ids(war_room_id),
                                   case_graph_parse_include(request.args.get('include')),
                                   request.args.get('review_state') or 'accepted')
    except BusinessProcessingError as e:
        return response_api_error(e.get_message(), data=e.get_data())
    return response_api_success(graph)
