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

"""Fallback bug-report intake.

The SPA's "Report a bug" dialog prefers to submit reports through
the Sentry SDK (via `Sentry.captureFeedback`) — but only when the
operator has enabled error reporting and configured a DSN. When
reporting is off, this endpoint is the fallback: the report lands as
a structured `app.logger.warning` line and an activity-log row so
support/admin can find it after the fact.

Session-authed for any logged-in user (no permission gate). A tiny
in-process rate limit (5/min per user) keeps a runaway UI from
flooding the activity log — for multi-worker deployments this is
per-worker, but a single worker still enforces a meaningful ceiling.
"""
import threading
import time

from flask import Blueprint
from flask import Response
from flask import g
from flask import request

from app import app
from app.blueprints.access_controls import ac_api_requires
from app.blueprints.iris_user import iris_current_user
from app.blueprints.rest.api_doc import api_doc
from app.blueprints.rest.endpoints import response_api_error
from app.blueprints.rest.endpoints import response_api_success
from app.iris_engine.utils.tracker import track_activity


bug_reports_blueprint = Blueprint(
    'bug_reports_rest_v2', __name__, url_prefix='/bug-reports'
)


_RATE_WINDOW_SECONDS = 60
_RATE_LIMIT = 5
_rate_lock = threading.Lock()
# user_id → list[timestamp] within the window. Trimmed on every check.
_rate_state: dict = {}


def _rate_limited(user_id) -> bool:
    now = time.monotonic()
    with _rate_lock:
        history = _rate_state.setdefault(user_id, [])
        cutoff = now - _RATE_WINDOW_SECONDS
        history[:] = [t for t in history if t > cutoff]
        if len(history) >= _RATE_LIMIT:
            return True
        history.append(now)
        return False


_MAX_TITLE = 200
_MAX_DESCRIPTION = 10_000
_MAX_USER_AGENT = 500
_MAX_URL = 2000
_MAX_REQUEST_ID = 128


def _clip(value, limit: int) -> str:
    if not isinstance(value, str):
        return ''
    return value.strip()[:limit]


@bug_reports_blueprint.post('')
@ac_api_requires()
@api_doc(tags=['BugReports'], summary='Submit a manual bug report (fallback path)')
def bug_report_submit() -> Response:
    """Accept a bug report from the SPA and log it.

    Body: `{title, description, url?, user_agent?, request_id?}`.
    Screenshots aren't stored on this path — the Sentry `captureFeedback`
    route handles binary attachments. When reporting is disabled, the
    fallback deliberately keeps the payload light so the activity log
    stays useful.
    """
    if not request.is_json:
        return response_api_error('Invalid request')

    body = request.get_json(silent=True) or {}
    title = _clip(body.get('title'), _MAX_TITLE)
    description = _clip(body.get('description'), _MAX_DESCRIPTION)
    if not title or not description:
        return response_api_error('title and description are required')

    user_id = getattr(iris_current_user, 'id', None)
    if _rate_limited(user_id):
        return response_api_error(
            'Rate limit exceeded — please wait a moment before filing again',
            status=429,
        )

    fields = {
        'title': title,
        'description': description,
        'url': _clip(body.get('url'), _MAX_URL),
        'user_agent': _clip(body.get('user_agent'), _MAX_USER_AGENT),
        'request_id': _clip(body.get('request_id'), _MAX_REQUEST_ID),
        'server_request_id': g.get('request_id'),
        'user_id': user_id,
    }
    # `%s` with a positional so the format-safe path is used and any
    # `%` in the description doesn't blow up the logging call.
    app.logger.warning('User bug report: %s', fields)
    track_activity(f'Bug report filed: {title}', ctx_less=False)
    return response_api_success({'received': True})
