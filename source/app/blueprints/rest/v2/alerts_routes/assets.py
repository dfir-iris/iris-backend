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

from flask import Blueprint
from flask import request
from flask import session
from marshmallow.exceptions import ValidationError

from app.blueprints.access_controls import ac_api_requires
from app.blueprints.access_controls import ac_api_return_access_denied
from app.blueprints.access_controls import ac_current_user_has_customer_access
from app.blueprints.access_controls import ac_fast_check_current_user_has_case_access
from app.blueprints.iris_user import iris_current_user
from app.blueprints.rest.api_doc import api_doc
from app.blueprints.rest.endpoints import response_api_error
from app.blueprints.rest.endpoints import response_api_not_found
from app.blueprints.rest.endpoints import response_api_success
from app.business.alerts import alerts_get
from app.business.alerts import alerts_get_asset
from app.business.alerts import alerts_update_asset
from app.models.authorization import CaseAccessLevel
from app.models.authorization import Permissions
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError
from app.schema.marshables import CaseAssetsSchema


# The details an analyst documents on an alert's asset. Identity
# (asset_id, asset_uuid), ownership (user_id) and the case attachment
# (case_id) are deliberately absent — this route is reachable with
# alerts_write alone. See the matching note in iocs.py.
_ASSET_EDITABLE_FIELDS = (
    'asset_name',
    'asset_type_id',
    'asset_description',
    'asset_domain',
    'asset_ip',
    'asset_tags',
    'asset_enrichment',
)

_ASSET_OPAQUE_ACTIVITY_FIELDS = frozenset({
    'asset_description',
    'asset_enrichment',
})


def _editable_fields_only(payload):
    if not isinstance(payload, dict):
        return {}
    return {k: v for k, v in payload.items() if k in _ASSET_EDITABLE_FIELDS}


def _asset_update_activity(asset, request_data):
    """Describe what really changed, for the alert history. See iocs.py."""
    activity_data = []

    for key, value in request_data.items():
        old_value = getattr(asset, key, None)

        if type(old_value) is int:
            old_value = str(old_value)
        if type(value) is int:
            value = str(value)

        if old_value == value:
            continue

        if key in _ASSET_OPAQUE_ACTIVITY_FIELDS:
            activity_data.append(f'"{key}"')
        else:
            activity_data.append(f'"{key}" from "{old_value}" to "{value}"')

    return activity_data


class AlertAssetsOperations:

    def __init__(self):
        self._schema = CaseAssetsSchema(exclude=['alerts'])

    def update(self, alert_identifier, identifier):
        try:
            alert = alerts_get(
                iris_current_user,
                (session.get('permissions') or 0),
                alert_identifier,
                fallback_customer_access=ac_current_user_has_customer_access
            )
            asset = alerts_get_asset(alert, identifier)

        except ObjectNotFoundError:
            return response_api_not_found()

        # Once escalated, the alert's asset row belongs to a case as well
        # — editing it from the alert must not bypass the case ACL.
        if asset.case_id is not None and not ac_fast_check_current_user_has_case_access(
                asset.case_id, [CaseAccessLevel.full_access]):
            return ac_api_return_access_denied(caseid=asset.case_id)

        request_data = _editable_fields_only(request.get_json())
        if not request_data:
            return response_api_error('No editable asset field in the request')

        activity_data = _asset_update_activity(asset, request_data)

        try:
            updated_asset = self._schema.load(request_data, instance=asset, partial=True)
            result = alerts_update_asset(alert, updated_asset, activity_data)
            return response_api_success(self._schema.dump(result))

        except ValidationError as e:
            return response_api_error('Data error', data=e.messages)

        except BusinessProcessingError as e:
            return response_api_error(e.get_message(), data=e.get_data())


alerts_assets_blueprint = Blueprint('alerts_assets', __name__, url_prefix='/<int:alert_identifier>/assets')
alert_assets_operations = AlertAssetsOperations()


@alerts_assets_blueprint.put('/<int:identifier>')
@ac_api_requires(Permissions.alerts_write)
@api_doc(request=CaseAssetsSchema, response=CaseAssetsSchema, tags=['Alerts'],
         summary='Update the details of an asset attached to an alert')
def update_alert_asset(alert_identifier, identifier):
    return alert_assets_operations.update(alert_identifier, identifier)
