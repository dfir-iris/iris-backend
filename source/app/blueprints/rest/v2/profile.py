#  IRIS Source Code
#  Copyright (C) 2024 - DFIR-IRIS
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
from marshmallow import ValidationError

from app.iris_engine.access_control.iris_user import iris_current_user
from app.blueprints.rest.endpoints import response_api_success
from app.blueprints.rest.endpoints import response_api_error
from app.blueprints.access_controls import ac_api_requires
from app.business.users import users_get
from app.business.users import users_update
from app.schema.marshables import UserSchemaForAPIV2


class ProfileOperations:

    def __init__(self):
        self._schema = UserSchemaForAPIV2()
        self._update_request_schema = UserSchemaForAPIV2(exclude=['user_is_service_account', 'user_active', 'uuid'])

    def update(self):
        try:
            user = users_get(iris_current_user.id)
            request_data = request.get_json()
            request_data['user_id'] = iris_current_user.id
            user = self._update_request_schema.load(request_data, instance=user, partial=True)
            user = users_update(user, request_data.get('user_password'))
            result = self._schema.dump(user)
            return response_api_success(result)
        except ValidationError as e:
            return response_api_error('Data error', data=e.messages)


profile_operations = ProfileOperations()
profile_blueprint = Blueprint('profile_rest_v2', __name__, url_prefix='/me')


@profile_blueprint.put('')
@ac_api_requires()
def update_profile():
    return profile_operations.update()
