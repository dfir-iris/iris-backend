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

"""Vulnerabilities tracked by a war room, independently of the asset
findings of its cases (see business/war_room_vulnerabilities.py). The
scope matrix (GET /scope/vulnerabilities) lists them alongside the
findings."""

from flask import Blueprint
from flask import request

from app.blueprints.access_controls import ac_api_requires
from app.blueprints.access_controls import ac_api_requires_vulnerabilities
from app.blueprints.iris_user import iris_current_user
from app.blueprints.rest.api_doc import api_doc
from app.blueprints.rest.endpoints import response_api_created
from app.blueprints.rest.endpoints import response_api_deleted
from app.blueprints.rest.endpoints import response_api_error
from app.blueprints.rest.endpoints import response_api_not_found
from app.blueprints.rest.endpoints import response_api_success
from app.blueprints.rest.v2.war_rooms.access import require_war_room_read
from app.blueprints.rest.v2.war_rooms.access import require_war_room_write
from app.business.war_room_vulnerabilities import war_room_vulnerabilities_list
from app.business.war_room_vulnerabilities import war_room_vulnerabilities_track
from app.business.war_room_vulnerabilities import war_room_vulnerabilities_untrack
from app.business.war_room_vulnerabilities import war_room_vulnerabilities_update
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError


war_rooms_vulnerabilities_blueprint = Blueprint(
    'war_rooms_vulnerabilities_rest_v2', __name__,
    url_prefix='/<int:war_room_id>/vulnerabilities'
)


def _json_body():
    raw = request.get_json(silent=True)
    return raw if isinstance(raw, dict) else None


@war_rooms_vulnerabilities_blueprint.get('')
@ac_api_requires()
@ac_api_requires_vulnerabilities()
@api_doc(tags=['WarRoomScope'], summary='List the vulnerabilities the war room tracks')
def list_tracked_vulnerabilities(war_room_id):
    err = require_war_room_read(war_room_id)
    if err is not None:
        return err
    return response_api_success(war_room_vulnerabilities_list(war_room_id))


@war_rooms_vulnerabilities_blueprint.post('')
@ac_api_requires()
@ac_api_requires_vulnerabilities(create=True)
@api_doc(tags=['WarRoomScope'],
         summary='Track a vulnerability on the war room, by vulnerability_id or identifier (quick add)')
def track_vulnerability(war_room_id):
    err = require_war_room_write(war_room_id)
    if err is not None:
        return err
    raw = _json_body()
    if raw is None:
        return response_api_error('Invalid request')
    try:
        result, created = war_room_vulnerabilities_track(war_room_id, raw, iris_current_user.id)
    except ObjectNotFoundError:
        return response_api_not_found()
    except BusinessProcessingError as e:
        return response_api_error(e.get_message())
    return response_api_created(result) if created else response_api_success(result)


@war_rooms_vulnerabilities_blueprint.patch('/<int:vulnerability_id>')
@ac_api_requires()
@ac_api_requires_vulnerabilities(create=True)
@api_doc(tags=['WarRoomScope'], summary='Update the note of a tracked vulnerability')
def update_tracked_vulnerability(war_room_id, vulnerability_id):
    err = require_war_room_write(war_room_id)
    if err is not None:
        return err
    raw = _json_body()
    if raw is None:
        return response_api_error('Invalid request')
    try:
        return response_api_success(war_room_vulnerabilities_update(war_room_id, vulnerability_id, raw))
    except ObjectNotFoundError:
        return response_api_not_found()
    except BusinessProcessingError as e:
        return response_api_error(e.get_message())


@war_rooms_vulnerabilities_blueprint.delete('/<int:vulnerability_id>')
@ac_api_requires()
@ac_api_requires_vulnerabilities(create=True)
@api_doc(tags=['WarRoomScope'], summary='Stop tracking a vulnerability (its findings are kept)')
def untrack_vulnerability(war_room_id, vulnerability_id):
    err = require_war_room_write(war_room_id)
    if err is not None:
        return err
    try:
        war_room_vulnerabilities_untrack(war_room_id, vulnerability_id)
    except ObjectNotFoundError:
        return response_api_not_found()
    return response_api_deleted()
