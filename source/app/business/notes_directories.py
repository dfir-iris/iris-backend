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

from app.db import db
from app.iris_engine.utils.tracker import track_activity
from app.models.models import NoteDirectory
from app.models.errors import ObjectNotFoundError
from app.datamgmt.case.case_notes_db import get_case_root_directory
from app.datamgmt.case.case_notes_db import get_directory
from app.datamgmt.case.case_notes_db import delete_directory
from app.datamgmt.case.case_notes_db import paginate_notes_directories
from app.models.pagination_parameters import PaginationParameters

# Name given to the directory created on the fly when a note has to be
# written into a case whose tree is still empty.
DEFAULT_ROOT_DIRECTORY_NAME = 'Notes'


def notes_directories_filter(case_identifier: int, pagination_parameters: PaginationParameters):
    return paginate_notes_directories(case_identifier, pagination_parameters)


def notes_directories_create(directory: NoteDirectory):
    db.session.add(directory)
    db.session.commit()

    track_activity(f'added directory "{directory.name}"', caseid=directory.case_id)


def notes_directories_get_or_create_root(case_identifier: int) -> NoteDirectory:
    """Return the case's top-level directory, creating one if needed.

    Cases only get a note tree when a case template pre-populates it or
    when a user creates a folder by hand, so a note-writing caller that
    doesn't pick a directory has nowhere to put its note. Rather than
    failing, materialise the top-level directory the first time one is
    needed — same thing the user would have done in the UI.
    """
    root = get_case_root_directory(case_identifier)
    if root is not None:
        return root

    root = NoteDirectory(name=DEFAULT_ROOT_DIRECTORY_NAME, parent_id=None, case_id=case_identifier)
    notes_directories_create(root)
    return root


def notes_directories_get(identifier) -> NoteDirectory:
    directory = get_directory(identifier)
    if not directory:
        raise ObjectNotFoundError()
    return directory


def notes_directories_update(directory: NoteDirectory):
    db.session.commit()

    track_activity(f'modified directory "{directory.name}"', caseid=directory.case_id)


def notes_directories_delete(directory: NoteDirectory):
    delete_directory(directory)
    track_activity(f'deleted directory "{directory.name}"', caseid=directory.case_id)
