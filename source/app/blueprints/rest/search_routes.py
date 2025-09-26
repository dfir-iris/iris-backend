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

from app.models.authorization import Permissions
from app.blueprints.access_controls import ac_api_requires
from app.blueprints.responses import response_success
from app.business.search import search

search_rest_blueprint = Blueprint('search_rest', __name__)


@search_rest_blueprint.route('/search', methods=['POST'])
@ac_api_requires(Permissions.search_across_cases)
def search_file_post():

    jsdata = request.get_json()
    search_value = jsdata.get('search_value')
    search_type = jsdata.get('search_type')

    files = search(search_type, search_value)

    return response_success('Results fetched', files)
