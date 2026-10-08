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

"""v2 endpoints for the asset flag taxonomy. Listing is open to any
authenticated user; changes require server administrator."""

from flask import Blueprint
from flask import request

from app.blueprints.access_controls import ac_api_requires
from app.blueprints.rest.api_doc import api_doc
from app.blueprints.rest.endpoints import response_api_created
from app.blueprints.rest.endpoints import response_api_deleted
from app.blueprints.rest.endpoints import response_api_error
from app.blueprints.rest.endpoints import response_api_not_found
from app.blueprints.rest.endpoints import response_api_success
from app.business.asset_flags import asset_flags_apply_preset
from app.business.asset_flags import asset_flags_create
from app.business.asset_flags import asset_flags_delete
from app.business.asset_flags import asset_flags_list
from app.business.asset_flags import asset_flags_reorder
from app.business.asset_flags import asset_flags_update
from app.models.authorization import Permissions
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError


def _body():
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else {}


def _handle(operation, created=False):
    try:
        result = operation()
    except ObjectNotFoundError:
        return response_api_not_found()
    except BusinessProcessingError as e:
        return response_api_error(e.get_message(), data=e.get_data())
    if created:
        return response_api_created(result)
    return response_api_success(result)


asset_flags_blueprint = Blueprint(
    'asset_flags_rest_v2', __name__,
    url_prefix='/asset-flags'
)


@asset_flags_blueprint.get('')
@ac_api_requires()
@api_doc(tags=['ManageAssetFlags'], summary='List asset flags, ordered, with their usage count')
def list_asset_flags_route():
    return _handle(asset_flags_list)


@asset_flags_blueprint.post('')
@ac_api_requires(Permissions.server_administrator)
@api_doc(response_shape='created', tags=['ManageAssetFlags'], summary='Create an asset flag')
def create_asset_flag_route():
    body = _body()
    return _handle(lambda: asset_flags_create(body), created=True)


@asset_flags_blueprint.post('/reorder')
@ac_api_requires(Permissions.server_administrator)
@api_doc(tags=['ManageAssetFlags'], summary='Reorder the asset flags (ids must list every flag once)')
def reorder_asset_flags_route():
    body = _body()
    return _handle(lambda: asset_flags_reorder(body.get('ids')))


@asset_flags_blueprint.post('/presets/<string:preset>')
@ac_api_requires(Permissions.server_administrator)
@api_doc(tags=['ManageAssetFlags'], summary='Replace the asset flags with a preset (only when no flag is set on an asset)')
def apply_asset_flags_preset_route(preset):
    return _handle(lambda: asset_flags_apply_preset(preset))


@asset_flags_blueprint.put('/<int:identifier>')
@ac_api_requires(Permissions.server_administrator)
@api_doc(tags=['ManageAssetFlags'], summary='Update an asset flag (fields left out keep their value)')
def update_asset_flag_route(identifier):
    body = _body()
    return _handle(lambda: asset_flags_update(identifier, body))


@asset_flags_blueprint.delete('/<int:identifier>')
@ac_api_requires(Permissions.server_administrator)
@api_doc(response_shape='deleted', tags=['ManageAssetFlags'], summary='Delete an asset flag that is not set on any asset')
def delete_asset_flag_route(identifier):
    try:
        asset_flags_delete(identifier)
    except ObjectNotFoundError:
        return response_api_not_found()
    except BusinessProcessingError as e:
        return response_api_error(e.get_message(), data=e.get_data())
    return response_api_deleted()
