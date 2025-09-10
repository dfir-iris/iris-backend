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
from marshmallow import ValidationError

from app.iris_engine.access_control.iris_user import iris_current_user
from app.blueprints.access_controls import ac_api_requires
from app.blueprints.rest.endpoints import response_api_paginated
from app.blueprints.rest.endpoints import response_api_not_found
from app.blueprints.rest.endpoints import response_api_created
from app.blueprints.rest.endpoints import response_api_error
from app.blueprints.rest.parsing import parse_pagination_parameters
from app.business.comments import comments_get_filtered_by_note
from app.business.comments import comments_create_for_note
from app.business.notes import notes_get
from app.business.errors import ObjectNotFoundError
from app.schema.marshables import CommentSchema
from app.iris_engine.access_control.utils import ac_fast_check_current_user_has_case_access
from app.models.authorization import CaseAccessLevel


class CommentsOperations:

    def __init__(self):
        self._schema = CommentSchema()

    @staticmethod
    def _get_note(note_identifier, possible_case_access_levels):
        note = notes_get(note_identifier)
        if not ac_fast_check_current_user_has_case_access(note.note_case_id, possible_case_access_levels):
            raise ObjectNotFoundError()
        return note


    def get(self, note_identifier):
        try:
            note = self._get_note([CaseAccessLevel.read_only, CaseAccessLevel.full_access])

            pagination_parameters = parse_pagination_parameters(request)

            comments = comments_get_filtered_by_note(note, pagination_parameters)
            return response_api_paginated(self._schema, comments)
        except ObjectNotFoundError:
            return response_api_not_found()

    def create(self, note_identifier):
        try:
            note = self._get_note(note_identifier, [CaseAccessLevel.full_access])

            comment = self._schema.load(request.get_json())
            comments_create_for_note(iris_current_user, note, comment)

            result = self._schema.dump(comment)
            return response_api_created(result)
        except ValidationError as e:
            return response_api_error('Data error', data=e.normalized_messages())
        except ObjectNotFoundError:
            return response_api_not_found()


notes_comments_blueprint = Blueprint('notes_comments', __name__, url_prefix='/<int:note_identifier>/comments')
comments_operations = CommentsOperations()


@notes_comments_blueprint.get('')
@ac_api_requires()
def get_notes_comments(note_identifier):
    return comments_operations.get(note_identifier)

@notes_comments_blueprint.post('')
@ac_api_requires()
def create_notes_comment(note_identifier):
    return comments_operations.create(note_identifier)
