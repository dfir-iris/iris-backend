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

from app.blueprints.rest.endpoints import response_api_created
from app.blueprints.rest.endpoints import response_api_error
from app.blueprints.rest.endpoints import response_api_success
from app.blueprints.rest.endpoints import response_api_not_found
from app.blueprints.rest.endpoints import response_api_deleted
from app.blueprints.access_controls import ac_api_requires
from app.models.authorization import Permissions
from app.schema.marshables import CustomerSchema
from app.business.errors import ObjectNotFoundError
from app.business.customers import customers_create_with_user
from app.business.customers import customers_get
from app.business.customers import customers_update
from app.business.customers import customers_delete
from app.blueprints.iris_user import iris_current_user


class Customers:

    def __init__(self):
        self._schema = CustomerSchema()

    def create(self):
        try:
            request_data = request.get_json()
            customer = self._schema.load(request_data)
            customers_create_with_user(iris_current_user, customer)
            result = self._schema.dump(customer)
            return response_api_created(result)
        except ValidationError as e:
            return response_api_error('Data error', data=e.messages)

    def read(self, identifier):
        try:
            customer = customers_get(identifier)
            result = self._schema.dump(customer)
            return response_api_success(result)
        except ObjectNotFoundError:
            return response_api_not_found()

    def update(self, identifier):
        try:
            customer = customers_get(identifier)
            request_data = request.get_json()
            request_data['customer_id'] = identifier
            updated_customer = self._schema.load(request_data, instance=customer)
            customers_update()
            result = self._schema.dump(updated_customer)
            return response_api_success(result)
        except ValidationError as e:
            return response_api_error('Data error', data=e.messages)
        except ObjectNotFoundError:
            return response_api_not_found()

    def delete(self, identifier):
        try:
            customer = customers_get(identifier)
            customers_delete(customer)
            return response_api_deleted()

        except ObjectNotFoundError:
            return response_api_not_found()


customers_blueprint = Blueprint('customers_rest_v2', __name__, url_prefix='/customers')

customers = Customers()


@customers_blueprint.post('')
@ac_api_requires(Permissions.customers_write)
def create_customer():
    return customers.create()


@customers_blueprint.get('/<int:identifier>')
@ac_api_requires(Permissions.customers_read)
def get_customer(identifier):
    return customers.read(identifier)


@customers_blueprint.put('/<int:identifier>')
@ac_api_requires(Permissions.customers_write)
def put_customer(identifier):
    return customers.update(identifier)


@customers_blueprint.delete('/<int:identifier>')
@ac_api_requires(Permissions.customers_write)
def delete_user(identifier):
    return customers.delete(identifier)
