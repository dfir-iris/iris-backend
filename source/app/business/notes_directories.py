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

from app import db
from app.iris_engine.utils.tracker import track_activity
from app.models.models import NoteDirectory
from app.datamgmt.case.case_notes_db import get_directory
from app.business.errors import ObjectNotFoundError
from app.business.errors import BusinessProcessingError


def notes_directories_create(directory: NoteDirectory):
    db.session.add(directory)
    db.session.commit()

    track_activity(f'added directory "{directory.name}"', caseid=directory.case_id)


def notes_directories_get(identifier) -> NoteDirectory:
    directory = get_directory(identifier)
    if not directory:
        raise ObjectNotFoundError()
    return directory


def notes_directories_get_in_case(identifier, case_identifier) -> NoteDirectory:
    directory = notes_directories_get(identifier)
    if directory.case_id != case_identifier:
        raise BusinessProcessingError(f'Note directory {directory.id} does not belong to case {case_identifier}')
    return directory


def notes_directories_update(directory: NoteDirectory):
    db.session.commit()

    track_activity(f'modified directory "{directory.name}"', caseid=directory.case_id)
