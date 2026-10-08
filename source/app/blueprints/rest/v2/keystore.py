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

"""v2 endpoints for the AI workflow keystore.

Personal entries need `ai_workflows_write`, shared entries need
`server_administrator`. Secret values are write-only: responses carry
`"value": null, "has_value": true`, and an update that omits the value
keeps the stored one. See `app.business.ai_keystore` for the rules.
"""

from flask import Blueprint
from flask import request

from app.blueprints.access_controls import ac_api_requires
from app.blueprints.access_controls import ac_current_user_has_permission
from app.blueprints.iris_user import iris_current_user
from app.blueprints.rest.api_doc import api_doc
from app.blueprints.rest.endpoints import response_api_created
from app.blueprints.rest.endpoints import response_api_deleted
from app.blueprints.rest.endpoints import response_api_error
from app.blueprints.rest.endpoints import response_api_not_found
from app.blueprints.rest.endpoints import response_api_success
from app.business.ai_keystore import AiKeystoreForbiddenError
from app.business.ai_keystore import ai_keystore_create
from app.business.ai_keystore import ai_keystore_delete
from app.business.ai_keystore import ai_keystore_list
from app.business.ai_keystore import ai_keystore_update
from app.models.authorization import Permissions
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError


def _body():
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else {}


def _flags() -> dict:
    return {
        'is_admin': ac_current_user_has_permission(Permissions.server_administrator),
        'can_write': ac_current_user_has_permission(Permissions.ai_workflows_write),
    }


def _handle(operation, created=False):
    try:
        result = operation()
    except ObjectNotFoundError:
        return response_api_not_found()
    except AiKeystoreForbiddenError as e:
        return response_api_error(e.get_message(), status=403)
    except BusinessProcessingError as e:
        return response_api_error(e.get_message(), data=e.get_data())
    if created:
        return response_api_created(result)
    return response_api_success(result)


keystore_blueprint = Blueprint('keystore_rest_v2', __name__, url_prefix='/keystore')


@keystore_blueprint.get('')
@ac_api_requires(Permissions.ai_workflows_read, Permissions.ai_workflows_write, Permissions.server_administrator)
@api_doc(tags=['AiKeystore'], summary='List the keystore entries the user can see (secret values are never returned)')
def list_keystore_entries_route():
    is_admin = _flags()['is_admin']
    return _handle(lambda: ai_keystore_list(iris_current_user.id, is_admin=is_admin))


@keystore_blueprint.post('')
@ac_api_requires(Permissions.ai_workflows_write, Permissions.server_administrator)
@api_doc(response_shape='created', tags=['AiKeystore'], summary='Create a personal or shared keystore entry')
def create_keystore_entry_route():
    body = _body()
    flags = _flags()
    return _handle(lambda: ai_keystore_create(body, iris_current_user.id, **flags), created=True)


@keystore_blueprint.put('/<int:identifier>')
@ac_api_requires(Permissions.ai_workflows_write, Permissions.server_administrator)
@api_doc(tags=['AiKeystore'], summary='Update a keystore entry (an omitted value keeps the stored one)')
def put_keystore_entry_route(identifier):
    body = _body()
    flags = _flags()
    return _handle(lambda: ai_keystore_update(identifier, body, iris_current_user.id, **flags))


@keystore_blueprint.delete('/<int:identifier>')
@ac_api_requires(Permissions.ai_workflows_write, Permissions.server_administrator)
@api_doc(response_shape='deleted', tags=['AiKeystore'], summary='Delete a keystore entry')
def delete_keystore_entry_route(identifier):
    flags = _flags()
    try:
        ai_keystore_delete(identifier, iris_current_user.id, **flags)
    except ObjectNotFoundError:
        return response_api_not_found()
    except AiKeystoreForbiddenError as e:
        return response_api_error(e.get_message(), status=403)
    return response_api_deleted()
