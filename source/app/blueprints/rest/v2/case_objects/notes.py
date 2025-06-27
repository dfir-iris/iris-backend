#  IRIS Source Code
#  Copyright (C) 2024 - DFIR-IRIS
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
from app.schema.marshables import CaseNoteSchema
from app.models.authorization import CaseAccessLevel
from app.models.models import Notes
from app.iris_engine.access_control.utils import ac_fast_check_current_user_has_case_access
from app.business.notes import notes_create
from app.business.notes import notes_get
from app.business.notes import notes_update
from app.business.notes import notes_delete
from app.business.cases import cases_exists
from app.business.errors import BusinessProcessingError
from app.business.errors import ObjectNotFoundError
from app.iris_engine.module_handler.module_handler import call_deprecated_on_preload_modules_hook


class NotesCRUD:

    def __init__(self):
        self._schema = CaseNoteSchema()

    def create(self, case_identifier):
        if not cases_exists(case_identifier):
            return response_api_not_found()

        if not ac_fast_check_current_user_has_case_access(case_identifier, [CaseAccessLevel.full_access]):
            return ac_api_return_access_denied(caseid=case_identifier)

        try:
            request_data = call_deprecated_on_preload_modules_hook('on_preload_note_create',
                                                                   request.get_json(), case_identifier)
            note = notes_create(request_data, case_identifier)

            return response_api_created(self._schema.dump(note))

        except BusinessProcessingError as e:
            return response_api_error(e.get_message(), data=e.get_data())


notesOperations = NotesCRUD()
case_notes_blueprint = Blueprint('case_notes',
                                 __name__,
                                 url_prefix='/<int:case_identifier>/notes')


@case_notes_blueprint.post('')
@ac_api_requires()
def create_note(case_identifier):
    return notesOperations.create(case_identifier)


@case_notes_blueprint.get('/<int:identifier>')
@ac_api_requires()
def get_note(case_identifier, identifier):

    try:
        note = notes_get(identifier)
        _check_note_and_case_identifier_match(note, case_identifier)

        if not ac_fast_check_current_user_has_case_access(note.note_case_id, [CaseAccessLevel.read_only, CaseAccessLevel.full_access]):
            return ac_api_return_access_denied(caseid=note.note_case_id)

        schema = CaseNoteSchema()
        return response_api_success(schema.dump(note))
    except ObjectNotFoundError:
        return response_api_not_found()
    except BusinessProcessingError as e:
        return response_api_error(e.get_message())


@case_notes_blueprint.put('<int:identifier>')
@ac_api_requires()
def update_note(case_identifier, identifier):
    try:
        note = notes_get(identifier)
        if not ac_fast_check_current_user_has_case_access(note.note_case_id, [CaseAccessLevel.full_access]):
            return ac_api_return_access_denied(caseid=note.note_case_id)
        _check_note_and_case_identifier_match(note, case_identifier)

        note = notes_update(note, request.get_json())

        schema = CaseNoteSchema()
        result = schema.dump(note)
        return response_api_success(result)

    except ObjectNotFoundError:
        return response_api_not_found()

    except BusinessProcessingError as e:
        return response_api_error(e.get_message(), data=e.get_data())


@case_notes_blueprint.delete('<int:identifier>')
@ac_api_requires()
def delete_note(case_identifier, identifier):
    try:
        note = notes_get(identifier)
        if not ac_fast_check_current_user_has_case_access(note.note_case_id, [CaseAccessLevel.full_access]):
            return ac_api_return_access_denied(caseid=note.note_case_id)
        _check_note_and_case_identifier_match(note, case_identifier)

        notes_delete(note)
        return response_api_deleted()

    except ObjectNotFoundError:
        return response_api_not_found()


def _check_note_and_case_identifier_match(note: Notes, case_identifier):
    if note.note_case_id != case_identifier:
        raise ObjectNotFoundError
