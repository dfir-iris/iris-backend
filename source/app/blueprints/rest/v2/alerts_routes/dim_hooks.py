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

"""v2 alert-scoped DIM hook invoker.

`POST /api/v2/alerts/dim-hooks/invoke` queues the `on_manual_trigger_alert`
hook against a batch of alerts — the buttons a module adds to an alert.

Alerts are not case objects, hence a route of their own rather than a
`type: alert` branch on `/api/v2/cases/<cid>/dim-hooks/invoke`: there is
no case to scope the targets by, so each one is loaded through
`alerts_get`, which applies the same customer-access check as every
other alert route. Targets the caller cannot see are reported in `logs`
exactly like targets that don't exist — a user must not be able to tell
the two apart.
"""

from flask import Blueprint
from flask import request
from flask import session

from app.blueprints.access_controls import ac_api_requires
from app.blueprints.access_controls import ac_current_user_has_customer_access
from app.blueprints.iris_user import iris_current_user
from app.blueprints.rest.api_doc import api_doc
from app.blueprints.rest.endpoints import response_api_error
from app.blueprints.rest.endpoints import response_api_success
from app.business.alerts import alerts_get
from app.business.dim_hooks import invoke_hook_for_alerts
from app.models.authorization import Permissions
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError


alerts_dim_hooks_blueprint = Blueprint('alerts_dim_hooks_rest_v2', __name__)


@alerts_dim_hooks_blueprint.post('/dim-hooks/invoke')
@ac_api_requires(Permissions.alerts_write)
@api_doc(tags=['Alerts'], summary='Invoke a DIM hook against alerts')
def invoke_alerts_dim_hook():
    payload = request.get_json(silent=True) or {}
    targets = payload.get('targets') or []

    if not targets:
        return response_api_error('Missing targets')
    if not isinstance(targets, list):
        return response_api_error('Invalid targets')

    permissions = session.get('permissions') or 0
    logs = []
    alerts = []
    for raw in targets:
        try:
            identifier = int(raw)
        except (TypeError, ValueError):
            return response_api_error('Invalid target')

        try:
            alerts.append(alerts_get(
                iris_current_user,
                permissions,
                identifier,
                fallback_customer_access=ac_current_user_has_customer_access,
            ))
        except ObjectNotFoundError:
            logs.append(f'Alert ID {identifier} not found')

    try:
        result = invoke_hook_for_alerts(
            hook_name=payload.get('hook_name'),
            hook_ui_name=payload.get('hook_ui_name'),
            module_name=payload.get('module_name'),
            alerts=alerts,
            logs=logs,
        )
    except BusinessProcessingError as e:
        return response_api_error(e.get_message())

    body = {'queued': result.queued}
    if result.logs:
        body['logs'] = result.logs

    return response_api_success(data=body)
