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
from app.business.alerts import alerts_get_ioc
from app.business.alerts import alerts_update_ioc
from app.models.authorization import CaseAccessLevel
from app.models.authorization import Permissions
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError
from app.schema.marshables import IocSchema


# The details an analyst documents on an alert's IOC. Everything else an
# IOC carries — its identity (ioc_id, ioc_uuid), its ownership (user_id)
# and above all its case attachment (case_id) — stays out: this route is
# reachable with alerts_write alone and must not become a way to move an
# IOC between cases or to rewrite its audit trail.
_IOC_EDITABLE_FIELDS = (
    'ioc_value',
    'ioc_type_id',
    'ioc_tlp_id',
    'ioc_description',
    'ioc_tags',
    'ioc_enrichment',
)

# Free-text/JSON fields whose *content* has no place in a one-line
# activity entry — the history records that they changed, not to what.
_IOC_OPAQUE_ACTIVITY_FIELDS = frozenset({
    'ioc_description',
    'ioc_enrichment',
})


def _editable_fields_only(payload):
    if not isinstance(payload, dict):
        return {}
    return {k: v for k, v in payload.items() if k in _IOC_EDITABLE_FIELDS}


def _ioc_update_activity(ioc, request_data):
    """Describe what really changed, for the alert history.

    The UI PUTs the whole form back, so comparing against the stored
    values is what keeps an edit of the description alone from being
    reported as an edit of every field on the dialog.
    """
    activity_data = []

    for key, value in request_data.items():
        old_value = getattr(ioc, key, None)

        # The payload carries JSON scalars while the model holds native
        # types, so 3 and "3" have to compare equal here or an untouched
        # select would be reported as a change on every save.
        if type(old_value) is int:
            old_value = str(old_value)
        if type(value) is int:
            value = str(value)

        if old_value == value:
            continue

        if key in _IOC_OPAQUE_ACTIVITY_FIELDS:
            activity_data.append(f'"{key}"')
        else:
            activity_data.append(f'"{key}" from "{old_value}" to "{value}"')

    return activity_data


class AlertIocsOperations:

    def __init__(self):
        self._schema = IocSchema()

    def update(self, alert_identifier, identifier):
        try:
            alert = alerts_get(
                iris_current_user,
                (session.get('permissions') or 0),
                alert_identifier,
                fallback_customer_access=ac_current_user_has_customer_access
            )
            ioc = alerts_get_ioc(alert, identifier)

        except ObjectNotFoundError:
            return response_api_not_found()

        # Escalation hands the alert's IOC row over to the case instead of
        # copying it (create_case_from_alert), so from then on this very
        # object is case data. alerts_write alone must not be enough to
        # edit it — that would be a way around the case access control.
        if ioc.case_id is not None and not ac_fast_check_current_user_has_case_access(
                ioc.case_id, [CaseAccessLevel.full_access]):
            return ac_api_return_access_denied(caseid=ioc.case_id)

        request_data = _editable_fields_only(request.get_json())
        if not request_data:
            return response_api_error('No editable IOC field in the request')

        # Built before the load: `load(instance=ioc)` mutates the IOC in
        # place, and the old values are needed to diff against.
        activity_data = _ioc_update_activity(ioc, request_data)

        # IocSchema re-validates the value against its type's regex and
        # reads both from the payload, so a partial update has to carry
        # the unchanged half of the pair explicitly.
        request_data.setdefault('ioc_value', ioc.ioc_value)
        request_data.setdefault('ioc_type_id', ioc.ioc_type_id)

        try:
            updated_ioc = self._schema.load(request_data, instance=ioc, partial=True)
            result = alerts_update_ioc(alert, updated_ioc, activity_data)
            return response_api_success(self._schema.dump(result))

        except ValidationError as e:
            return response_api_error('Data error', data=e.messages)

        except BusinessProcessingError as e:
            return response_api_error(e.get_message(), data=e.get_data())


alerts_iocs_blueprint = Blueprint('alerts_iocs', __name__, url_prefix='/<int:alert_identifier>/iocs')
alert_iocs_operations = AlertIocsOperations()


@alerts_iocs_blueprint.put('/<int:identifier>')
@ac_api_requires(Permissions.alerts_write)
@api_doc(request=IocSchema, response=IocSchema, tags=['Alerts'],
         summary='Update the details of an IOC attached to an alert')
def update_alert_ioc(alert_identifier, identifier):
    return alert_iocs_operations.update(alert_identifier, identifier)
