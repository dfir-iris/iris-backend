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

from app.blueprints.rest.endpoints import response_api_success
from app.blueprints.access_controls import ac_api_requires


class ProfileOperations:

    def update(self):
        return response_api_success(None)


profile_operations = ProfileOperations()
profile_blueprint = Blueprint('profile_rest_v2', __name__, url_prefix='/me')


@profile_blueprint.put('')
@ac_api_requires()
def update_profile():
    return profile_operations.update()
