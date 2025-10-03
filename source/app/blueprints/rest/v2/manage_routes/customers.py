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


from app.blueprints.rest.endpoints import response_api_created
from app.blueprints.access_controls import ac_api_requires
from app.models.authorization import Permissions
from app.schema.marshables import CustomerSchema


class Customers:

    def __init__(self):
        self._schema = CustomerSchema()

    def create(self):
        return response_api_created(None)

customers_blueprint = Blueprint('customers_rest_v2', __name__, url_prefix='/customers')

customers = Customers()

@customers_blueprint.post('')
@ac_api_requires(Permissions.customers_write)
def create_customer():
    return customers.create()
