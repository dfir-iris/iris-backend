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

from typing import Any


def api_doc(
    *,
    request=None,
    response=None,
    response_status: int = 200,
    response_shape: str = 'raw',
    tags: list[str] | None = None,
    summary: str | None = None,
    query_params: list[tuple] | None = None,
) -> Any:
    """Attach OpenAPI metadata to a view function.

    The generator reads `view.__apidoc__` and turns it into an
    OpenAPI operation. Everything is optional — omitted fields fall
    back to introspection (docstring for summary/description, URL
    rule for path parameters).

    Args:
        request: marshmallow Schema class describing the JSON body.
        response: marshmallow Schema class describing the success body.
        response_status: HTTP status the `response` schema applies to.
        response_shape: 'raw' | 'paginated' | 'created' | 'deleted'.
            Controls the envelope wrapper the generator emits. 'raw'
            matches response_api_success (bare data), 'paginated'
            matches response_api_paginated (total/data/... envelope),
            'created' → 201 with bare data, 'deleted' → 204 no body.
        tags: OpenAPI tags for grouping.
        summary: Overrides the docstring-derived summary.
        query_params: List of query-string parameters the view reads
            via `request.args.get(...)`. Each entry is a tuple:
                (name, type)                          → optional param, no description
                (name, type, description)             → optional param with description
                (name, type, description, required)   → required if last elt is True
            `type` is one of 'string' | 'integer' | 'number' | 'boolean' |
            'date' | 'date-time'. Repeatable query keys (e.g. Flask's
            `getlist(...)`) can use 'string[]' / 'integer[]' etc. to
            emit as arrays with `style: form, explode: true`.
    """

    def decorator(view):
        view.__apidoc__ = {
            'request': request,
            'response': response,
            'response_status': response_status,
            'response_shape': response_shape,
            'tags': tags or [],
            'summary': summary,
            'query_params': query_params or [],
        }
        return view

    return decorator
