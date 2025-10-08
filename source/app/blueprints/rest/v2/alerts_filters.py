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
from marshmallow import ValidationError

from app.blueprints.access_controls import ac_api_requires
from app.blueprints.rest.endpoints import response_api_created
from app.blueprints.rest.endpoints import response_api_error
from app.blueprints.iris_user import iris_current_user
from app.schema.marshables import SavedFilterSchema


class AlertsFiltersOperations:

    def __init__(self):
        self._schema = SavedFilterSchema()

    def create(self):
        saved_filter_schema = SavedFilterSchema()
 
        request_data = request.get_json()
        request_data ['created_by'] = iris_current_user.id

        try:
            new_saved_filter = saved_filter_schema.load(request_data)
            return response_api_created(saved_filter_schema.dump(new_saved_filter))

        except ValidationError as e:
            return response_api_error('Data error', e.messages)


alerts_filters_blueprint = Blueprint('alerts_filters_rest_v2', __name__, url_prefix='/alerts-filters')
alerts_filters_operations = AlertsFiltersOperations()


@alerts_filters_blueprint.post('')
@ac_api_requires()
def create_alert_filter():
    return alerts_filters_operations.create()
