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

"""v2 endpoints of the vulnerability findings of a case
(`/api/v2/cases/<case_identifier>/vulnerabilities`).

Reads need read access on the case and `vulnerabilities_read`, changes
need full access and `vulnerabilities_create`. A
war-room decision referenced by a finding must come from a war room the
caller can read (and that the case is attached to); otherwise the answer
is the same as for an unknown decision, so its existence does not leak.
"""

from flask import Blueprint
from flask import request

from app.blueprints.access_controls import ac_api_requires
from app.blueprints.access_controls import ac_api_requires_vulnerabilities
from app.blueprints.access_controls import ac_api_return_access_denied
from app.blueprints.access_controls import ac_fast_check_current_user_has_case_access
from app.blueprints.iris_user import iris_current_user
from app.blueprints.rest.api_doc import api_doc
from app.blueprints.rest.endpoints import response_api_created
from app.blueprints.rest.endpoints import response_api_deleted
from app.blueprints.rest.endpoints import response_api_error
from app.blueprints.rest.endpoints import response_api_not_found
from app.blueprints.rest.endpoints import response_api_success
from app.blueprints.rest.v2.war_rooms.access import require_war_room_read
from app.blueprints.rest.v2.war_rooms.access import war_room_redact_decisions
from app.business.asset_stages import asset_stages_decision_war_room_id
from app.business.cases import cases_exists
from app.business.vulnerability_findings import vulnerability_findings_case_create
from app.business.vulnerability_findings import vulnerability_findings_case_delete
from app.business.vulnerability_findings import vulnerability_findings_case_get_public
from app.business.vulnerability_findings import vulnerability_findings_case_history
from app.business.vulnerability_findings import vulnerability_findings_case_list
from app.business.vulnerability_findings import vulnerability_findings_case_update
from app.models.authorization import CaseAccessLevel
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError

_READ = [CaseAccessLevel.read_only, CaseAccessLevel.full_access]
_WRITE = [CaseAccessLevel.full_access]
_UNKNOWN_DECISION = 'Decision not found in a war room this case is attached to'


def _body():
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else {}


def _handle(case_identifier, access_level, operation, created=False):
    if not cases_exists(case_identifier):
        return response_api_not_found()
    if not ac_fast_check_current_user_has_case_access(case_identifier, access_level):
        return ac_api_return_access_denied(caseid=case_identifier)
    try:
        result = operation()
    except ObjectNotFoundError:
        return response_api_not_found()
    except BusinessProcessingError as e:
        return response_api_error(e.get_message(), data=e.get_data())
    if created:
        return response_api_created(result)
    if result is None:
        return response_api_deleted()
    return response_api_success(result)


def _check_decision_readable(body):
    """Refuse a decision the caller cannot read, like an unknown one."""
    decision_id = body.get('decision_id')
    if decision_id is None:
        return
    war_room_id = asset_stages_decision_war_room_id(decision_id)
    if war_room_id is None or require_war_room_read(war_room_id) is not None:
        raise BusinessProcessingError(_UNKNOWN_DECISION)


case_vulnerabilities_blueprint = Blueprint(
    'case_vulnerabilities_rest_v2', __name__,
    url_prefix='/<int:case_identifier>/vulnerabilities'
)


@case_vulnerabilities_blueprint.get('')
@ac_api_requires()
@ac_api_requires_vulnerabilities()
@api_doc(tags=['CaseVulnerabilities'], summary='List the vulnerability findings of a case, worst first',
         query_params=[('asset_id', 'integer', 'Only the findings of this asset'),
                       ('vulnerability_id', 'integer', 'Only the findings of this catalogue entry'),
                       ('status', 'string', 'Comma-separated remediation statuses'),
                       ('status_group', 'string', 'open, fixed or dismissed'),
                       ('exploitation', 'string', 'Comma-separated exploitation statuses'),
                       ('severity', 'string', 'Comma-separated severities'),
                       ('kev', 'boolean', 'Only (or no) CISA KEV entries'),
                       ('overdue', 'boolean', 'Only open findings past their due date'),
                       ('search', 'string', 'Substring of the identifier, title, asset or component')])
def list_case_vulnerabilities_route(case_identifier):
    return _handle(case_identifier, _READ, lambda: vulnerability_findings_case_list(case_identifier, request.args))


@case_vulnerabilities_blueprint.post('')
@ac_api_requires()
@ac_api_requires_vulnerabilities(create=True)
@api_doc(response_shape='created', tags=['CaseVulnerabilities'],
         summary='Record a vulnerability on one or more case assets (vulnerability_id, or identifier '
                 'for a quick add)')
def create_case_vulnerability_route(case_identifier):
    body = _body()

    def _create():
        _check_decision_readable(body)
        return vulnerability_findings_case_create(case_identifier, body, iris_current_user.id)

    return _handle(case_identifier, _WRITE, _create, created=True)


@case_vulnerabilities_blueprint.get('/<int:finding_id>')
@ac_api_requires()
@ac_api_requires_vulnerabilities()
@api_doc(tags=['CaseVulnerabilities'], summary='Get a vulnerability finding of a case')
def get_case_vulnerability_route(case_identifier, finding_id):
    return _handle(case_identifier, _READ, lambda: vulnerability_findings_case_get_public(case_identifier, finding_id))


@case_vulnerabilities_blueprint.put('/<int:finding_id>')
@ac_api_requires()
@ac_api_requires_vulnerabilities(create=True)
@api_doc(tags=['CaseVulnerabilities'],
         summary='Update a vulnerability finding (fields left out keep their value)')
def update_case_vulnerability_route(case_identifier, finding_id):
    body = _body()

    def _update():
        _check_decision_readable(body)
        return vulnerability_findings_case_update(case_identifier, finding_id, body, iris_current_user.id)

    return _handle(case_identifier, _WRITE, _update)


@case_vulnerabilities_blueprint.delete('/<int:finding_id>')
@ac_api_requires()
@ac_api_requires_vulnerabilities(create=True)
@api_doc(response_shape='deleted', tags=['CaseVulnerabilities'], summary='Delete a vulnerability finding')
def delete_case_vulnerability_route(case_identifier, finding_id):
    return _handle(case_identifier, _WRITE, lambda: vulnerability_findings_case_delete(case_identifier, finding_id))


@case_vulnerabilities_blueprint.get('/<int:finding_id>/history')
@ac_api_requires()
@ac_api_requires_vulnerabilities()
@api_doc(tags=['CaseVulnerabilities'], summary='Change history of a vulnerability finding, newest first')
def get_case_vulnerability_history_route(case_identifier, finding_id):
    return _handle(case_identifier, _READ, lambda: war_room_redact_decisions(
        vulnerability_findings_case_history(case_identifier, finding_id)))
