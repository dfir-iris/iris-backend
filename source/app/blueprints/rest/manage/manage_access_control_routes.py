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

from app.business.users import users_reset_mfa
from app.datamgmt.manage.manage_users_db import get_user
from app.iris_engine.access_control.utils import ac_recompute_all_users_effective_ac
from app.iris_engine.access_control.utils import ac_recompute_effective_ac
from app.iris_engine.access_control.utils import ac_trace_effective_user_permissions
from app.iris_engine.access_control.utils import ac_trace_user_effective_cases_access_2
from app.iris_engine.demo_builder import protect_demo_mode_user
from app.models.authorization import Permissions
from app.blueprints.access_controls import ac_api_requires
from app.blueprints.access_controls import ac_api_return_access_denied
from app.blueprints.responses import response_success
from app.blueprints.rest.endpoints import endpoint_deprecated

manage_ac_rest_blueprint = Blueprint('access_control_rest', __name__)


@manage_ac_rest_blueprint.route('/manage/access-control/recompute-effective-users-ac', methods=['GET'])
@endpoint_deprecated('POST', '/api/v2/manage/access-control/recompute-all')
@ac_api_requires(Permissions.server_administrator)
def manage_ac_compute_effective_all_ac():

    ac_recompute_all_users_effective_ac()

    return response_success('Updated')


@manage_ac_rest_blueprint.route('/manage/access-control/recompute-effective-user-ac/<int:cur_id>', methods=['GET'])
@endpoint_deprecated('POST', '/api/v2/manage/users/{identifier}/recompute-access')
@ac_api_requires(Permissions.server_administrator)
def manage_ac_compute_effective_ac(cur_id):

    ac_recompute_effective_ac(cur_id)

    return response_success('Updated')


@manage_ac_rest_blueprint.route('/manage/access-control/reset-mfa/<int:cur_id>', methods=['GET'])
@endpoint_deprecated('POST', '/api/v2/manage/users/{identifier}/mfa/reset')
@ac_api_requires(Permissions.server_administrator)
def manage_ac_reset_mfa(cur_id):

    user = get_user(cur_id)
    if user is not None and protect_demo_mode_user(user):
        return ac_api_return_access_denied()

    users_reset_mfa(cur_id)

    return response_success('Updated')


@manage_ac_rest_blueprint.route('/manage/access-control/audit/users/<int:cur_id>', methods=['GET'])
@endpoint_deprecated('GET', '/api/v2/manage/users/{identifier}/audit')
@ac_api_requires(Permissions.server_administrator)
def manage_ac_audit_user(cur_id):
    user_audit = {
        'access_audit': ac_trace_user_effective_cases_access_2(cur_id),
        'permissions_audit': ac_trace_effective_user_permissions(cur_id)
    }

    return response_success(data=user_audit)
