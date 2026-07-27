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

from pathlib import Path

from flask import Blueprint
from flask import Response
from flask import send_file

from app import app
from app.blueprints.access_controls import ac_api_requires
from app.blueprints.rest.api_doc import api_doc
from app.blueprints.rest.endpoints import response_api_success


api_blueprint = Blueprint('api_rest_v2', __name__)

# The generator emits the spec next to this file so operators only
# have one artifact to ship. `Path(__file__)` resolves against the
# unpacked install location regardless of container layout.
_OPENAPI_SPEC = Path(__file__).resolve().parent.parent / 'openapi.generated.yaml'


@api_blueprint.get('/ping')
@ac_api_requires()
@api_doc(tags=['Meta'], summary='Health check')
def api_ping():
    return response_api_success('pong')


@api_blueprint.get('/versions')
@ac_api_requires()
@api_doc(tags=['Meta'], summary='Report IRIS + API version')
def api_versions():
    return response_api_success({
        'iris_current': app.config.get('IRIS_VERSION'),
        'api_min': app.config.get('API_MIN_VERSION'),
        'api_current': app.config.get('API_MAX_VERSION'),
    })


@api_blueprint.get('/openapi.yaml')
@ac_api_requires()
@api_doc(tags=['Meta'], summary='Get the OpenAPI spec (YAML)')
def api_openapi_spec():
    """Serve the OpenAPI 3.1 spec generated at build time.

    Gated by the same auth as any other v2 endpoint — the spec exposes
    the entire request/response shape of the API, so we don't hand it
    out to unauthenticated callers. Session-authed browsers pick it up
    via the docs page below.
    """
    return send_file(
        _OPENAPI_SPEC,
        mimetype='application/yaml',
        as_attachment=False,
        download_name='iris-openapi.yaml',
    )


# Redoc is a single ~1 MB JS bundle served from a CDN. We ship a
# minimal HTML shim that points it at /api/v2/openapi.yaml — no build
# step, no local vendoring. The `<redoc>` element is auto-upgraded by
# the script tag. Kept as a constant so the response is byte-stable
# and cache-friendly.
_REDOC_HTML = """<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8"/>
    <title>IRIS API reference</title>
    <meta name="viewport" content="width=device-width, initial-scale=1"/>
    <link href="https://fonts.googleapis.com/css?family=Montserrat:300,400,700|Roboto:300,400,700" rel="stylesheet"/>
    <style>body { margin: 0; padding: 0; }</style>
</head>
<body>
    <redoc spec-url="/api/v2/openapi.yaml"></redoc>
    <script src="https://cdn.redoc.ly/redoc/latest/bundles/redoc.standalone.js"></script>
</body>
</html>
"""


@api_blueprint.get('/docs')
@ac_api_requires()
@api_doc(tags=['Meta'], summary='Interactive API reference (Redoc)')
def api_docs():
    """Serve the Redoc-rendered API documentation.

    Points at /api/v2/openapi.yaml above; both endpoints require
    the same session/API-key auth so operators can browse the docs
    while logged in but public visitors don't get a free tour of
    the internal surface.
    """
    return Response(_REDOC_HTML, mimetype='text/html')
