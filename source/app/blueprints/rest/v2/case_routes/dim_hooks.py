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

"""v2 case-scoped DIM hook invoker.

`POST /api/v2/cases/<cid>/dim-hooks/invoke` queues a manual module hook
against a batch of targets (assets, iocs, notes, tasks, events, ...).
Case access is verified via the standard `ac_fast_check_current_user_has_case_access`
guard used by every other case-scoped v2 route.
"""

from flask import Blueprint
from flask import request

from app.blueprints.access_controls import ac_api_requires
from app.blueprints.access_controls import ac_api_return_access_denied
from app.blueprints.access_controls import ac_fast_check_current_user_has_case_access
from app.blueprints.rest.api_doc import api_doc
from app.blueprints.rest.endpoints import response_api_error
from app.blueprints.rest.endpoints import response_api_success
from app.business.dim_hooks import invoke_hook_for_case
from app.models.authorization import CaseAccessLevel
from app.models.errors import BusinessProcessingError


case_dim_hooks_blueprint = Blueprint(
    'case_dim_hooks_rest_v2',
    __name__,
    url_prefix='/<int:case_identifier>/dim-hooks',
)


@case_dim_hooks_blueprint.post('/invoke')
@ac_api_requires()
@api_doc(tags=['CaseDimHooks'], summary='Invoke a DIM hook for a case')
def invoke_dim_hook(case_identifier):
    if not ac_fast_check_current_user_has_case_access(
        case_identifier, [CaseAccessLevel.full_access]
    ):
        return ac_api_return_access_denied(caseid=case_identifier)

    payload = request.get_json(silent=True) or {}

    try:
        result = invoke_hook_for_case(
            caseid=case_identifier,
            hook_name=payload.get('hook_name'),
            hook_ui_name=payload.get('hook_ui_name'),
            module_name=payload.get('module_name'),
            data_type=payload.get('type'),
            targets=payload.get('targets') or [],
        )
    except BusinessProcessingError as e:
        return response_api_error(e.get_message())

    body = {'queued': result.queued}
    if result.logs:
        body['logs'] = result.logs

    return response_api_success(data=body)
