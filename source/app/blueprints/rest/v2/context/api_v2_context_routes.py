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
from app.blueprints.rest.endpoints import response_api_success
from app.iris_engine.access_control.iris_user import iris_current_user
from app.datamgmt.context.context_db import ctx_search_user_cases

api_v2_context_blueprint = Blueprint('context_api_v2', __name__, url_prefix='/api/v2')


# TODO put this endpoint back once it adheres to the conventions (verb in URL)
#@api_v2_context_blueprint.route('/context/search-cases', methods=['GET'])
@ac_api_requires()
def cases_context_search_v2():
    """
    V2: Search for user cases based on a query parameter (e.g., investigations not closed).
    """
    search = request.args.get('q')
    data = ctx_search_user_cases(search, iris_current_user.id, max_results=100)
    return response_api_success(data=data)
