#  IRIS Source Code
#  Copyright (C) 2025 - DFIR-IRIS
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

from app.blueprints.access_controls import ac_api_requires
from app.blueprints.rest.endpoints import response_api_created
from app.blueprints.rest.endpoints import response_api_success
from app.blueprints.rest.endpoints import response_api_deleted
from app.blueprints.rest.endpoints import response_api_error
from app.blueprints.rest.endpoints import response_api_not_found
from app.blueprints.access_controls import ac_api_return_access_denied
from app.business.events import events_create
from app.business.events import events_get
from app.business.events import events_update
from app.business.events import events_delete
from app.models.cases import CasesEvent
from app.schema.marshables import EventSchema
from app.business.errors import BusinessProcessingError
from app.business.errors import ObjectNotFoundError
from app.business.cases import cases_exists
from app.iris_engine.access_control.utils import ac_fast_check_current_user_has_case_access
from app.iris_engine.utils.collab import notify
from app.models.authorization import CaseAccessLevel


case_events_blueprint = Blueprint('case_events_rest_v2', __name__, url_prefix='/<int:case_identifier>/events')


@case_events_blueprint.post('')
@ac_api_requires()
def create_event(case_identifier):
    if not cases_exists(case_identifier):
        return response_api_not_found()
    if not ac_fast_check_current_user_has_case_access(case_identifier, [CaseAccessLevel.full_access]):
        return ac_api_return_access_denied(caseid=case_identifier)

    try:
        event = events_create(case_identifier, request.get_json())
        schema = EventSchema()
        result = schema.dump(event)
        notify(case_identifier, 'events', 'updated', event.event_id, object_data=result)

        return response_api_created(result)
    except BusinessProcessingError as e:
        return response_api_error(e.get_message(), data=e.get_data())


@case_events_blueprint.get('/<int:identifier>')
@ac_api_requires()
def get_event(case_identifier, identifier):
    if not cases_exists(case_identifier):
        return response_api_not_found()

    try:
        event = events_get(identifier)
        _check_event_and_case_identifier_match(event, case_identifier)
        if not ac_fast_check_current_user_has_case_access(event.case_id, [CaseAccessLevel.read_only, CaseAccessLevel.full_access]):
            return ac_api_return_access_denied(caseid=event.case_id)

        schema = EventSchema()
        result = schema.dump(event)
        result['event_category_id'] = event.category[0].id if event.category else None
        return response_api_success(result)
    except ObjectNotFoundError:
        return response_api_not_found()
    except BusinessProcessingError as e:
        return response_api_error(e.get_message(), data=e.get_data())


@case_events_blueprint.put('/<int:identifier>')
@ac_api_requires()
def update_event(case_identifier, identifier):
    if not cases_exists(case_identifier):
        return response_api_not_found()

    try:
        event = events_get(identifier)
        if not ac_fast_check_current_user_has_case_access(event.case_id, [CaseAccessLevel.full_access]):
            return ac_api_return_access_denied(caseid=event.case_id)
        _check_event_and_case_identifier_match(event, case_identifier)

        event = events_update(event, request.get_json())

        schema = EventSchema()
        result = schema.dump(event)
        notify(case_identifier, 'events', 'updated', identifier, object_data=result)

        return response_api_success(result)
    except ObjectNotFoundError:
        return response_api_not_found()
    except BusinessProcessingError as e:
        return response_api_error(e.get_message(), data=e.get_data())


@case_events_blueprint.delete('/<int:identifier>')
@ac_api_requires()
def delete_event(case_identifier, identifier):
    event = events_get(identifier)
    events_delete(event)

    return response_api_deleted()


def _check_event_and_case_identifier_match(event: CasesEvent, case_identifier):
    if event.case_id != case_identifier:
        raise BusinessProcessingError(f'Event {event.event_id} does not belong to case {case_identifier}')
