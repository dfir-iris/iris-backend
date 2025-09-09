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
from app.blueprints.rest.endpoints import response_api_paginated
from app.blueprints.rest.endpoints import response_api_not_found
from app.blueprints.rest.parsing import parse_pagination_parameters
from app.blueprints.access_controls import ac_api_return_access_denied
from app.business.comments import comments_get_filtered_by_task
from app.business.tasks import tasks_get
from app.business.errors import ObjectNotFoundError
from app.schema.marshables import CommentSchema
from app.iris_engine.access_control.utils import ac_fast_check_current_user_has_case_access
from app.models.authorization import CaseAccessLevel


class CommentsOperations:

    def __init__(self):
        self._schema = CommentSchema()

    def get(self, task_identifier):
        try:
            task = tasks_get(task_identifier)
            if not ac_fast_check_current_user_has_case_access(task.task_case_id,
                                                              [CaseAccessLevel.read_only, CaseAccessLevel.full_access]):
                return ac_api_return_access_denied(caseid=task.task_case_id)

            pagination_parameters = parse_pagination_parameters(request)

            comments = comments_get_filtered_by_task(task, pagination_parameters)
            return response_api_paginated(self._schema, comments)
        except ObjectNotFoundError:
            return response_api_not_found()


tasks_comments_blueprint = Blueprint('tasks_comments', __name__, url_prefix='/<int:task_identifier>/comments')
comments_operations = CommentsOperations()


@tasks_comments_blueprint.get('')
@ac_api_requires()
def get_tasks_comments(task_identifier):
    return comments_operations.get(task_identifier)
