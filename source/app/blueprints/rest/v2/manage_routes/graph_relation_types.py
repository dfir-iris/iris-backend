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

"""v2 endpoints for the relation types of the investigation graph.
Listing is open to any authenticated user; changes require server
administrator."""

from flask import Blueprint
from flask import request

from app.blueprints.access_controls import ac_api_requires
from app.blueprints.rest.api_doc import api_doc
from app.blueprints.rest.endpoints import response_api_created
from app.blueprints.rest.endpoints import response_api_deleted
from app.blueprints.rest.endpoints import response_api_error
from app.blueprints.rest.endpoints import response_api_not_found
from app.blueprints.rest.endpoints import response_api_success
from app.business.case_graph_relation_types import case_graph_relation_types_create
from app.business.case_graph_relation_types import case_graph_relation_types_delete
from app.business.case_graph_relation_types import case_graph_relation_types_list
from app.business.case_graph_relation_types import case_graph_relation_types_reorder
from app.business.case_graph_relation_types import case_graph_relation_types_update
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


graph_relation_types_blueprint = Blueprint(
    'graph_relation_types_rest_v2', __name__,
    url_prefix='/graph-relation-types'
)


@graph_relation_types_blueprint.get('')
@ac_api_requires()
@api_doc(tags=['ManageGraphRelationTypes'], summary='List the graph relation types, ordered, with their usage count')
def list_graph_relation_types_route():
    return _handle(case_graph_relation_types_list)


@graph_relation_types_blueprint.post('')
@ac_api_requires(Permissions.server_administrator)
@api_doc(response_shape='created', tags=['ManageGraphRelationTypes'], summary='Create a graph relation type')
def create_graph_relation_type_route():
    body = _body()
    return _handle(lambda: case_graph_relation_types_create(body), created=True)


@graph_relation_types_blueprint.post('/reorder')
@ac_api_requires(Permissions.server_administrator)
@api_doc(tags=['ManageGraphRelationTypes'], summary='Reorder the graph relation types (ids must list every type once)')
def reorder_graph_relation_types_route():
    body = _body()
    return _handle(lambda: case_graph_relation_types_reorder(body.get('ids')))


@graph_relation_types_blueprint.put('/<int:identifier>')
@ac_api_requires(Permissions.server_administrator)
@api_doc(tags=['ManageGraphRelationTypes'], summary='Update a graph relation type (fields left out keep their value)')
def update_graph_relation_type_route(identifier):
    body = _body()
    return _handle(lambda: case_graph_relation_types_update(identifier, body))


@graph_relation_types_blueprint.delete('/<int:identifier>')
@ac_api_requires(Permissions.server_administrator)
@api_doc(response_shape='deleted', tags=['ManageGraphRelationTypes'],
         summary='Delete a custom graph relation type that no stored edge uses')
def delete_graph_relation_type_route(identifier):
    try:
        case_graph_relation_types_delete(identifier)
    except ObjectNotFoundError:
        return response_api_not_found()
    except BusinessProcessingError as e:
        return response_api_error(e.get_message(), data=e.get_data())
    return response_api_deleted()
