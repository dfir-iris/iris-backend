#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
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

"""v2 top-level DIM hooks endpoint.

Only the LIST route lives here — invoking a hook is always case-scoped
and is registered under `/api/v2/cases/<cid>/dim-hooks/invoke` by
`case_routes/dim_hooks.py`.
"""

from flask import Blueprint
from flask import request

from app.blueprints.access_controls import ac_api_requires
from app.blueprints.rest.api_doc import api_doc
from app.blueprints.rest.endpoints import response_api_error
from app.blueprints.rest.endpoints import response_api_success
from app.business.dim_hooks import list_hook_options_for


dim_hooks_blueprint = Blueprint('dim_hooks_rest_v2', __name__, url_prefix='/dim-hooks')


@dim_hooks_blueprint.get('')
@ac_api_requires()
@api_doc(tags=['DimHooks'], summary='List available module hooks')
def list_dim_hook_options():
    """Return every active module hook registered for the requested
    `target` type (e.g. `?target=ioc`).

    Naked JSON array of `{manual_hook_ui_name, hook_name, module_name}`.
    """
    target = (request.args.get('target') or '').strip()
    if not target:
        return response_api_error('Missing target query parameter')

    return response_api_success(data=list_hook_options_for(target))
