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
from marshmallow import ValidationError

from app.blueprints.iris_user import iris_current_user
from app.iris_engine.module_handler.module_handler import call_deprecated_on_preload_modules_hook
from app.schema.marshables import GlobalTasksSchema
from app.blueprints.access_controls import ac_api_requires
from app.blueprints.rest.endpoints import response_api_error
from app.blueprints.rest.endpoints import response_api_created
from app.blueprints.rest.endpoints import response_api_success
from app.blueprints.rest.endpoints import response_api_not_found
from app.business.global_tasks import global_tasks_create
from app.business.global_tasks import global_tasks_get
from app.models.errors import ObjectNotFoundError


class GlobalTasksOperations:

    def __init__(self):
        self._schema = GlobalTasksSchema()

    def create(self):
        request_data = call_deprecated_on_preload_modules_hook('global_task_create', request.get_json())
        try:
            global_task = self._schema.load(request_data)
            global_task = global_tasks_create(iris_current_user, global_task)
            result = self._schema.dump(global_task)
            return response_api_created(result)
        except ValidationError as e:
            return response_api_error('Data error', data=e.messages)

    def read(self, identifier):
        try:
            customer = global_tasks_get(identifier)
            result = self._schema.dump(customer)
            return response_api_success(result)
        except ObjectNotFoundError:
            return response_api_not_found()


global_tasks_blueprint = Blueprint('global_tasks_rest_v2', __name__, url_prefix='/global-tasks')

global_tasks_operations = GlobalTasksOperations()


@global_tasks_blueprint.post('')
@ac_api_requires()
def create_customer():
    return global_tasks_operations.create()


@global_tasks_blueprint.get('/<int:identifier>')
@ac_api_requires()
def get_customer(identifier):
    return global_tasks_operations.read(identifier)
