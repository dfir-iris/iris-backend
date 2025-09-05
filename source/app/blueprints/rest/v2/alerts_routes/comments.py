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
from app.models.authorization import Permissions
from app.blueprints.rest.endpoints import response_api_paginated
from app.blueprints.rest.endpoints import response_api_not_found
from app.blueprints.rest.endpoints import response_api_created
from app.blueprints.rest.parsing import parse_pagination_parameters
from app.schema.marshables import CommentSchema
from app.business.comments import comments_get_filtered_by_alert
from app.business.comments import comments_create_for_alert
from app.iris_engine.access_control.iris_user import iris_current_user
from app.business.errors import ObjectNotFoundError


class CommentsOperations:

    def __init__(self):
        self._schema = CommentSchema()

    def search(self, alert_identifier):
        pagination_parameters = parse_pagination_parameters(request)
        try:
            comments = comments_get_filtered_by_alert(iris_current_user, alert_identifier, pagination_parameters)
            return response_api_paginated(self._schema, comments)
        except ObjectNotFoundError:
            return response_api_not_found()

    def create(self, alert_identifier):
        comment = self._schema.load(request.get_json())
        comments_create_for_alert(comment, alert_identifier)
        result = self._schema.dump(comment)
        return response_api_created(result)


alerts_comments_blueprint = Blueprint('alerts_comments', __name__, url_prefix='/<int:alert_identifier>/comments')
comments_operations = CommentsOperations()


@alerts_comments_blueprint.get('')
@ac_api_requires(Permissions.alerts_read)
def get_alerts_comments(alert_identifier):
    return comments_operations.search(alert_identifier)


@alerts_comments_blueprint.post('')
@ac_api_requires(Permissions.alerts_write)
def create_alerts_comment(alert_identifier):
    return comments_operations.create(alert_identifier)
