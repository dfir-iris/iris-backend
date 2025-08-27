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

from app.blueprints.access_controls import ac_api_requires
from app.models.authorization import Permissions
from app.blueprints.rest.endpoints import response_api_paginated
from app.schema.marshables import CommentSchema
from app.business.comments import comments_get_filtered_by_alert


class CommentsOperations:

    def __init__(self):
        self._schema = CommentSchema()

    def get(self, alert_identifier):
        comments = comments_get_filtered_by_alert(alert_identifier)
        return response_api_paginated(self._schema, comments)


alerts_comments_blueprint = Blueprint('alerts_comments', __name__, url_prefix='/<int:alert_identifier>/comments')
comments_operations = CommentsOperations()


@alerts_comments_blueprint.get('')
@ac_api_requires(Permissions.alerts_read)
def get_alerts_comments(alert_identifier):
    return comments_operations.get(alert_identifier)
