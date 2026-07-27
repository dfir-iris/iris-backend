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

"""Runtime configuration surface for the SPA.

The SPA needs a small slice of the server's settings at boot time to
init browser-side integrations (e.g. the Sentry SDK for error
reporting). Rather than expose the full `ServerSettings` row (which
requires `server_administrator`), we serve a narrow, session-authed
digest here.

Session-auth-only (no permission gate) means:
  * Anonymous probes can't fingerprint whether error reporting is
    enabled, or read the frontend DSN.
  * Any logged-in user's browser can call this on boot without
    needing admin rights.

The backend DSN is intentionally NOT in this response — it's meant
for the Flask reporter, not the SPA.
"""
from decimal import Decimal

from flask import Blueprint
from flask import Response

from app import app
from app.blueprints.access_controls import ac_api_requires
from app.blueprints.rest.api_doc import api_doc
from app.blueprints.rest.endpoints import response_api_success
from app.business.server_settings import get_srv_settings


runtime_config_blueprint = Blueprint(
    'runtime_config_rest_v2', __name__, url_prefix='/runtime-config'
)


def _coerce_sample_rate(raw) -> float:
    """Sample-rate column is Numeric(3,2) — cast to a JSON-safe float."""
    if raw is None:
        return 1.0
    if isinstance(raw, Decimal):
        return float(raw)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 1.0


@runtime_config_blueprint.get('')
@ac_api_requires()
@api_doc(tags=['RuntimeConfig'],
         summary='Runtime config digest for the SPA (browser-safe subset)')
def runtime_config_get() -> Response:
    """Return the browser-safe subset of `ServerSettings`.

    Currently only the error-reporting slice. Future runtime toggles
    that the SPA needs at boot go under new top-level keys on this
    response so the SPA can consume them additively.
    """
    settings = get_srv_settings()
    enabled = bool(getattr(settings, 'error_reporting_enabled', False))
    frontend_dsn = getattr(settings, 'error_reporting_frontend_dsn', None)
    return response_api_success({
        'error_reporting': {
            # Only advertise "enabled" as true if a DSN is present —
            # otherwise the SPA has nothing to init the SDK against.
            'enabled': bool(enabled and frontend_dsn),
            'dsn': frontend_dsn if enabled else None,
            'environment': getattr(settings, 'error_reporting_environment', None),
            'sample_rate': _coerce_sample_rate(
                getattr(settings, 'error_reporting_sample_rate', None)),
            'release': f"iris@{app.config.get('IRIS_VERSION') or 'unknown'}",
        },
    })
