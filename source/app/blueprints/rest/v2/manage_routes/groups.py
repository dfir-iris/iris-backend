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
from marshmallow import ValidationError

from app.blueprints.rest.endpoints import response_api_created
from app.blueprints.rest.endpoints import response_api_success
from app.blueprints.rest.endpoints import response_api_error
from app.blueprints.rest.endpoints import response_api_not_found
from app.blueprints.rest.endpoints import response_api_deleted
from app.blueprints.access_controls import wrap_with_permission_checks
from app.schema.marshables import AuthorizationGroupSchema
from app.business.groups import groups_create
from app.business.groups import groups_get
from app.business.groups import groups_update
from app.business.groups import groups_delete
from app.models.authorization import Permissions
from app.business.errors import BusinessProcessingError
from app.business.errors import ObjectNotFoundError
from app.iris_engine.access_control.iris_user import iris_current_user
from app.iris_engine.access_control.utils import ac_flag_match_mask
from app.iris_engine.access_control.utils import ac_ldp_group_update


class Groups:

    def __init__(self):
        self._schema = AuthorizationGroupSchema()

    def _load(self, request_data, **kwargs):
        return self._schema.load(request_data, **kwargs)

    def create(self):
        try:
            request_data = request.get_json()
            group = self._load(request_data)
            group = groups_create(group)
            result = self._schema.dump(group)
            return response_api_created(result)
        except ValidationError as e:
            return response_api_error('Data error', data=e.messages)

    def get(self, identifier):
        try:
            group = groups_get(identifier)
            result = self._schema.dump(group)
            return response_api_success(result)
        except ObjectNotFoundError:
            return response_api_not_found()

    def update(self, identifier):
        try:
            group = groups_get(identifier)
            request_data = request.get_json()
            request_data['group_id'] = identifier
            updated_group = self._load(request_data, instance=group, partial=True)
            if not ac_flag_match_mask(request_data['group_permissions'], Permissions.server_administrator.value) and ac_ldp_group_update(iris_current_user.id):
                return response_api_error('That might not be a good idea Dave', data='Update the group permissions will lock you out')
            groups_update()
            result = self._schema.dump(updated_group)
            return response_api_success(result)

        except ValidationError as e:
            return response_api_error('Data error', data=e.messages)

        except ObjectNotFoundError:
            return response_api_not_found()

    def delete(self, identifier):
        try :
            group = groups_get(identifier)
            groups_delete(iris_current_user, group)
            return response_api_deleted()

        except ObjectNotFoundError:
            return response_api_not_found()
        except BusinessProcessingError as e:
            return response_api_error(e.get_message())


def create_groups_blueprint():
    blueprint = Blueprint('rest_v2_groups', __name__, url_prefix='/groups')
    groups = Groups()

    create_group = wrap_with_permission_checks(groups.create, Permissions.server_administrator)
    blueprint.add_url_rule('', view_func=create_group, methods=['POST'])

    get_group = wrap_with_permission_checks(groups.get, Permissions.server_administrator)
    blueprint.add_url_rule('/<int:identifier>', view_func=get_group, methods=['GET'])

    update_group = wrap_with_permission_checks(groups.update, Permissions.server_administrator)
    blueprint.add_url_rule('/<int:identifier>', view_func=update_group, methods=['PUT'])

    delete_group = wrap_with_permission_checks(groups.delete, Permissions.server_administrator)
    blueprint.add_url_rule('/<int:identifier>', view_func=delete_group, methods=['DELETE'])

    return blueprint
