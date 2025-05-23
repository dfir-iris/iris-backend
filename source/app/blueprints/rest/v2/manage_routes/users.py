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

from app.blueprints.rest.endpoints import response_api_success
from app.blueprints.access_controls import wrap_with_permission_checks
from app.models.authorization import Permissions
from app.schema.marshables import UserSchema


class Users:

    def __init__(self):
        self._schema = UserSchema()

    def _load(self, request_data):
        return self._schema.load(request_data)

    def create(self):
        return response_api_success(None)


def create_users_blueprint():
    blueprint = Blueprint('rest_v2_users', __name__, url_prefix='/users')
    users = Users()

    create_user = wrap_with_permission_checks(users.create, Permissions.server_administrator)
    blueprint.add_url_rule('', view_func=create_user, methods=['POST'])

    return blueprint
