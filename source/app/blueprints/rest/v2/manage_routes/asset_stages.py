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

"""v2 endpoints for the asset stage taxonomy. Listing is open to any
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
from app.business.asset_stages import asset_stages_apply_preset
from app.business.asset_stages import asset_stages_create
from app.business.asset_stages import asset_stages_delete
from app.business.asset_stages import asset_stages_list
from app.business.asset_stages import asset_stages_reorder
from app.business.asset_stages import asset_stages_update
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


asset_stages_blueprint = Blueprint(
    'asset_stages_rest_v2', __name__,
    url_prefix='/asset-stages'
)


@asset_stages_blueprint.get('')
@ac_api_requires()
@api_doc(tags=['ManageAssetStages'], summary='List asset stages, ordered, with their usage count')
def list_asset_stages_route():
    return _handle(asset_stages_list)


@asset_stages_blueprint.post('')
@ac_api_requires(Permissions.server_administrator)
@api_doc(response_shape='created', tags=['ManageAssetStages'], summary='Create an asset stage')
def create_asset_stage_route():
    body = _body()
    return _handle(lambda: asset_stages_create(body), created=True)


@asset_stages_blueprint.post('/reorder')
@ac_api_requires(Permissions.server_administrator)
@api_doc(tags=['ManageAssetStages'], summary='Reorder the asset stages (ids must list every stage once)')
def reorder_asset_stages_route():
    body = _body()
    return _handle(lambda: asset_stages_reorder(body.get('ids')))


@asset_stages_blueprint.post('/presets/<string:preset>')
@ac_api_requires(Permissions.server_administrator)
@api_doc(tags=['ManageAssetStages'], summary='Replace the asset stages with a preset (only when no stage is in use)')
def apply_asset_stages_preset_route(preset):
    return _handle(lambda: asset_stages_apply_preset(preset))


@asset_stages_blueprint.put('/<int:identifier>')
@ac_api_requires(Permissions.server_administrator)
@api_doc(tags=['ManageAssetStages'], summary='Update an asset stage (fields left out keep their value)')
def update_asset_stage_route(identifier):
    body = _body()
    return _handle(lambda: asset_stages_update(identifier, body))


@asset_stages_blueprint.delete('/<int:identifier>')
@ac_api_requires(Permissions.server_administrator)
@api_doc(response_shape='deleted', tags=['ManageAssetStages'], summary='Delete an asset stage that is not in use')
def delete_asset_stage_route(identifier):
    try:
        asset_stages_delete(identifier)
    except ObjectNotFoundError:
        return response_api_not_found()
    except BusinessProcessingError as e:
        return response_api_error(e.get_message(), data=e.get_data())
    return response_api_deleted()
