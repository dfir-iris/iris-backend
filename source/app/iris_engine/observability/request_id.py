#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 3 of the License, or (at your option) any later version.

"""Per-request correlation ID.

Every incoming request gets an `X-Request-Id` — either propagated
from the client (SPA sets it on every fetch) or freshly generated
here. The id is:

  * exposed to app code via `flask.g.request_id`
  * echoed on the response so the SPA can pair its own captures with
    the server's
  * tagged into the Sentry scope so the same id shows up on any
    crash event emitted for that request

The pair is registered from `app/__init__.py` next to the error-
reporter init. Adds negligible overhead per request.
"""
from __future__ import annotations

import uuid

from flask import Response
from flask import current_app
from flask import g
from flask import request


_HEADER = 'X-Request-Id'


def _sentry_set_tag(name: str, value: str) -> None:
    """Best-effort tag on the current Sentry scope.

    Wrapped so an ImportError (SDK not installed) or a
    no-op client silently does nothing.
    """
    try:
        import sentry_sdk
        sentry_sdk.set_tag(name, value)
    except Exception:
        pass


def _before_request() -> None:
    incoming = request.headers.get(_HEADER)
    request_id = incoming if incoming and len(incoming) <= 128 else uuid.uuid4().hex
    g.request_id = request_id
    _sentry_set_tag('request_id', request_id)
    if request.endpoint:
        _sentry_set_tag('iris_endpoint', request.endpoint)


def _after_request(response: Response) -> Response:
    request_id = g.get('request_id')
    if request_id:
        response.headers[_HEADER] = request_id
    return response


def register_request_id_middleware(app) -> None:
    """Wire the before/after handlers on `app`.

    Safe to call once at boot. Uses `app.before_request` /
    `app.after_request` so the ids are stamped for every route,
    including 404 / 500 error handlers.
    """
    app.before_request(_before_request)
    app.after_request(_after_request)
    # Silence the linter — `current_app` is imported so future
    # extensions of this module can bind it without another import.
    _ = current_app
