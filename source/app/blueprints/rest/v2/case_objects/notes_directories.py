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
from app.schema.marshables import CaseNoteDirectorySchema
from app.business.notes_directories import notes_directory_create


class NotesDirectories:

    def __init__(self):
        self._schema = CaseNoteDirectorySchema()

    def _load(self, request_data):
        return self._schema.load(request_data)

    def create(self, case_identifier):
        request_data = request.get_json()
        request_data.pop('id', None)
        request_data['case_id'] = case_identifier
        directory = self._load(request_data)

        notes_directory_create(directory)
        result = self._schema.dump(directory)

        return response_api_created(result)


notes_directories = NotesDirectories()
case_notes_directories_blueprint = Blueprint('case_notes_directories_rest_v2', __name__, url_prefix='/<int:case_identifier>/notes-directories')


@case_notes_directories_blueprint.post('')
@ac_api_requires()
def create_event(case_identifier):
    return notes_directories.create(case_identifier)
