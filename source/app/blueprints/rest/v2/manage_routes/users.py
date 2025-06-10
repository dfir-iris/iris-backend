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

from app.blueprints.access_controls import ac_api_requires
from app.blueprints.rest.endpoints import response_api_created
from app.blueprints.rest.endpoints import response_api_success
from app.blueprints.rest.endpoints import response_api_error
from app.blueprints.rest.endpoints import response_api_not_found
from app.schema.marshables import UserSchema
from app.models.authorization import Permissions
from app.business.errors import ObjectNotFoundError
from app.business.users import user_create
from app.business.users import user_get
from app.business.users import user_update

users_blueprint = Blueprint('users_rest_v2', __name__, url_prefix='/users')


def _load(request_data, **kwargs):
    user_schema = UserSchema()
    return user_schema.load(request_data, **kwargs)


def schema_without_fields(user):
    user_schema = UserSchema(exclude=('user_password',
                                    'mfa_secrets',
                                    'mfa_setup_complete',
                                    'webauthn_credentials',
                                    'has_deletion_confirmation',
                                    'has_mini_sidebar',
                                    'user_isadmin',
                                    'in_dark_mode'))
    data = user_schema.dump(user)
    return data


def add_infos_to_data(data, user_data):
    data['user_groups'] = user_data.group
    data['user_organisations'] = user_data.organisation
    data['user_permissions'] = user_data.effective_permissions
    data['user_customers'] = user_data.user_clients
    data['user_primary_organisation_id'] = user_data.primary_organisation_id
    data['user_cases_access'] = user_data.cases_access
    if user_data.user_api_key != '':
        data['user_api_key'] = user_data.user_api_key
    return data


@users_blueprint.post('')
@ac_api_requires(Permissions.server_administrator)
def create_users():

        try:
            request_data = request.get_json()
            request_data['user_id'] = 0
            request_data['active'] = request_data.get('active', True)
            user = _load(request_data)
            user = user_create(user, request_data['active'])
            user_schema = UserSchema()
            result = user_schema.dump(user)
            result['user_api_key'] = user.api_key
            del result['user_password']
            return response_api_created(result)
        except ValidationError as e:
            return response_api_error('Data error', data=e.messages)


@users_blueprint.get('/<int:identifier>')
@ac_api_requires(Permissions.server_administrator)
def get_users(identifier):

    try:
        data = user_get(identifier)
        data_without_fields = schema_without_fields(data.get_user())
        new_data = add_infos_to_data(data_without_fields, data)
        return response_api_success(new_data)
    except ObjectNotFoundError:
            return response_api_not_found()


@users_blueprint.put('/<int:identifier>')
@ac_api_requires(Permissions.server_administrator)
def put(identifier):

    try:
        data = user_get(identifier)
        request_data = request.get_json()
        request_data['user_id'] = identifier
        user_updated = _load(request_data, instance=data.get_user(), partial=True)
        user_update(user_updated, request_data.get('user_password'))
        data_without_fields = schema_without_fields(user_updated)
        new_data = add_infos_to_data(data_without_fields, data)
        return response_api_success(new_data)

    except ValidationError as e:
        return response_api_error('Data error', data=e.messages)

    except ObjectNotFoundError:
        return response_api_not_found()
