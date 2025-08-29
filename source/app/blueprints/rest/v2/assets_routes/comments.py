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
from app.business.comments import comments_get_filtered_by_asset
from app.business.assets import assets_get
from app.business.errors import ObjectNotFoundError
from app.schema.marshables import CommentSchema
from app.iris_engine.access_control.utils import ac_fast_check_current_user_has_case_access
from app.models.authorization import CaseAccessLevel


class CommentsOperations:

    def __init__(self):
        self._schema = CommentSchema()

    def get(self, asset_identifier):
        asset = assets_get(asset_identifier)
        if not ac_fast_check_current_user_has_case_access(asset.case_id,
                                                          [CaseAccessLevel.read_only, CaseAccessLevel.full_access]):
            return ac_api_return_access_denied(caseid=asset.case_id)

        pagination_parameters = parse_pagination_parameters(request)
        try:
            comments = comments_get_filtered_by_asset(asset_identifier, pagination_parameters)
            return response_api_paginated(self._schema, comments)
        except ObjectNotFoundError:
            return response_api_not_found()


assets_comments_blueprint = Blueprint('assets_comments', __name__, url_prefix='/<int:asset_identifier>/comments')
comments_operations = CommentsOperations()


@assets_comments_blueprint.get('')
@ac_api_requires()
def get_assets_comments(asset_identifier):
    return comments_operations.get(asset_identifier)
