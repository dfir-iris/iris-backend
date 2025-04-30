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

from flask import Blueprint
from flask import request

from app.blueprints.rest.endpoints import response_api_created
from app.blueprints.access_controls import ac_api_requires
from app.schema.marshables import AuthorizationGroupSchema
from app.business.groups import groups_create


class Groups:

    def __init__(self):
        self._schema = AuthorizationGroupSchema()

    def _load(self, request_data):
        return self._schema.load(request_data)

    def create(self):
        request_data = request.get_json()
        group = self._load(request_data)
        group = groups_create(group)
        result = self._schema.dump(group)
        return response_api_created(result)


groups = Groups()
groups_blueprint = Blueprint('rest_v2_groups', __name__, url_prefix='/groups')


@groups_blueprint.post('')
@ac_api_requires()
def create_event():
    return groups.create()
