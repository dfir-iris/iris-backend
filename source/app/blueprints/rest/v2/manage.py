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

from app.blueprints.rest.v2.manage_routes.groups import create_groups_blueprint
from app.blueprints.rest.v2.manage_routes.users import create_users_blueprint

manage_v2_blueprint = Blueprint('manage', __name__, url_prefix='/manage')

groups_blueprint = create_groups_blueprint()
manage_v2_blueprint.register_blueprint(groups_blueprint)

users_blueprint = create_users_blueprint()
manage_v2_blueprint.register_blueprint(users_blueprint)
