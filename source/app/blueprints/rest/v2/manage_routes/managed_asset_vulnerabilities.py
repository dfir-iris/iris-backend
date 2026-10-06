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

"""v2 endpoints of the vulnerability findings of a registry asset
(`/api/v2/manage/managed-assets/<id>/vulnerabilities`).

Same access rules as the registry itself: `asset_manager_read` to read,
`asset_manager_write` to change, and a 404 for an asset outside the
caller's customers; plus `vulnerabilities_read` / `vulnerabilities_create` (see `manage_routes/managed_assets.py`). The case
findings listed alongside come only from the sightings the caller can
see.
"""

from flask import Blueprint
from flask import request

from app.blueprints.access_controls import ac_api_requires
from app.blueprints.access_controls import ac_api_requires_vulnerabilities
from app.blueprints.access_controls import ac_current_user_permissions_mask
from app.blueprints.iris_user import iris_current_user
from app.blueprints.rest.api_doc import api_doc
from app.blueprints.rest.endpoints import response_api_created
from app.blueprints.rest.endpoints import response_api_deleted
from app.blueprints.rest.endpoints import response_api_error
from app.blueprints.rest.endpoints import response_api_not_found
from app.blueprints.rest.endpoints import response_api_success
from app.blueprints.rest.v2.war_rooms.access import war_room_redact_decisions
from app.business.managed_assets import managed_assets_get
from app.business.managed_assets import managed_assets_viewer_scope
from app.business.vulnerability_findings import vulnerability_findings_managed_create
from app.business.vulnerability_findings import vulnerability_findings_managed_delete
from app.business.vulnerability_findings import vulnerability_findings_managed_history
from app.business.vulnerability_findings import vulnerability_findings_managed_list
from app.business.vulnerability_findings import vulnerability_findings_managed_update
from app.models.authorization import Permissions
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError


def _body():
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else {}


def _scope():
    return managed_assets_viewer_scope(iris_current_user, ac_current_user_permissions_mask())


def _handle(identifier, operation, created=False):
    """Resolve the asset within the caller's scope, then run `operation(asset, scope)`."""
    try:
        scope = _scope()
        asset = managed_assets_get(identifier, scope)
        result = operation(asset, scope)
    except ObjectNotFoundError:
        return response_api_not_found()
    except BusinessProcessingError as e:
        return response_api_error(e.get_message(), data=e.get_data())
    if created:
        return response_api_created(result)
    if result is None:
        return response_api_deleted()
    return response_api_success(result)


managed_asset_vulnerabilities_blueprint = Blueprint(
    'managed_asset_vulnerabilities_rest_v2', __name__,
    url_prefix='/managed-assets/<int:identifier>/vulnerabilities'
)


@managed_asset_vulnerabilities_blueprint.get('')
@ac_api_requires(Permissions.asset_manager_read)
@ac_api_requires_vulnerabilities()
@api_doc(tags=['ManagedAssets'],
         summary='Vulnerability findings of a registry asset, and those of its visible case sightings',
         query_params=[('status', 'string', 'Comma-separated remediation statuses'),
                       ('status_group', 'string', 'open, fixed or dismissed'),
                       ('exploitation', 'string', 'Comma-separated exploitation statuses'),
                       ('severity', 'string', 'Comma-separated severities'),
                       ('kev', 'boolean', 'Only (or no) CISA KEV entries'),
                       ('overdue', 'boolean', 'Only open findings past their due date')])
def list_managed_asset_vulnerabilities_route(identifier):
    return _handle(identifier, lambda asset, scope: vulnerability_findings_managed_list(asset, request.args, scope))


@managed_asset_vulnerabilities_blueprint.post('')
@ac_api_requires(Permissions.asset_manager_write)
@ac_api_requires_vulnerabilities(create=True)
@api_doc(response_shape='created', tags=['ManagedAssets'],
         summary='Record a vulnerability on a registry asset (vulnerability_id, or identifier for a quick add)')
def create_managed_asset_vulnerability_route(identifier):
    body = _body()
    return _handle(identifier, lambda asset, _: vulnerability_findings_managed_create(
        asset, body, iris_current_user.id), created=True)


@managed_asset_vulnerabilities_blueprint.put('/<int:finding_id>')
@ac_api_requires(Permissions.asset_manager_write)
@ac_api_requires_vulnerabilities(create=True)
@api_doc(tags=['ManagedAssets'], summary='Update a registry vulnerability finding')
def update_managed_asset_vulnerability_route(identifier, finding_id):
    body = _body()
    return _handle(identifier, lambda asset, _: vulnerability_findings_managed_update(
        asset, finding_id, body, iris_current_user.id))


@managed_asset_vulnerabilities_blueprint.delete('/<int:finding_id>')
@ac_api_requires(Permissions.asset_manager_write)
@ac_api_requires_vulnerabilities(create=True)
@api_doc(response_shape='deleted', tags=['ManagedAssets'], summary='Delete a registry vulnerability finding')
def delete_managed_asset_vulnerability_route(identifier, finding_id):
    return _handle(identifier, lambda asset, _: vulnerability_findings_managed_delete(asset, finding_id))


@managed_asset_vulnerabilities_blueprint.get('/<int:finding_id>/history')
@ac_api_requires(Permissions.asset_manager_read)
@ac_api_requires_vulnerabilities()
@api_doc(tags=['ManagedAssets'], summary='Change history of a registry vulnerability finding')
def get_managed_asset_vulnerability_history_route(identifier, finding_id):
    return _handle(identifier, lambda asset, _: war_room_redact_decisions(
        vulnerability_findings_managed_history(asset, finding_id)))
