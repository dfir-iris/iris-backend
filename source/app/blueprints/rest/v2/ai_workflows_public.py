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

"""Public (no session, no API key) AI workflow endpoints.

External systems call these with a per-wait or per-workflow bearer
token, never with an IRIS identity. They deliberately carry no
`ac_api_requires` decorator and never touch `iris_current_user`: that
would make flask-login try the `Authorization` header as an IRIS API
key. Authentication is entirely in `app.business.ai_workflows_inbound`.

Any token / signature / replay / expiry / unknown-object problem gives
the same 401 `{"message": "Unauthorized"}`; unsafe JSON (NaN, Infinity,
integers outside 64 bits, nesting deeper than 32) or a missing entity id
gives 400, a body over `AI_WORKFLOWS_MAX_INBOUND_BYTES` gives 413, a
webhook trigger over the workflow `max_runs_per_hour` gives 429. An
accepted trigger always answers 202 `{"run_uuid", "status": "accepted"}`,
whether or not the workflow owner can access the entity the payload
names. Webhook signatures use the workflow signing secret, not the
bearer token. `request.remote_addr` is the client address (ProxyFix
trusts one proxy hop). nginx caps these paths at 1 MiB and rate limits
them per client address.
"""

from flask import Blueprint
from flask import request

from app.blueprints.responses import response
from app.blueprints.rest.api_doc import api_doc
from app.business.ai_workflows_inbound import AiWorkflowsInboundError
from app.business.ai_workflows_inbound import ai_workflows_inbound_callback
from app.business.ai_workflows_inbound import ai_workflows_inbound_max_bytes
from app.business.ai_workflows_inbound import ai_workflows_inbound_trigger


_MESSAGES = {
    400: 'Bad request',
    401: 'Unauthorized',
    413: 'Payload too large',
    429: 'Too many requests',
}


def _read_body() -> bytes:
    """At most max + 1 bytes, so an oversized body is detected without
    buffering all of it."""
    return request.stream.read(ai_workflows_inbound_max_bytes() + 1) or b''


def _refuse(error: AiWorkflowsInboundError):
    status = error.status
    if status >= 500:
        return response(500, data={'message': 'Internal error'})
    return response(status, data={'message': _MESSAGES.get(status, 'Unauthorized')})


def _inbound(operation, identifier):
    try:
        result = operation(identifier, request.headers, _read_body(), request.remote_addr,
                           content_length=request.content_length)
    except AiWorkflowsInboundError as e:
        return _refuse(e)
    return response(202, data=result)


ai_workflows_public_blueprint = Blueprint('ai_workflows_public_rest_v2', __name__, url_prefix='/ai-workflows')


@ai_workflows_public_blueprint.post('/callbacks/<wait_uuid>')
@api_doc(response_status=202, tags=['AiWorkflowsInbound'],
         summary='Resolve a pending AI workflow callback wait (bearer callback token, optional HMAC signature)')
def ai_workflows_callback_route(wait_uuid):
    return _inbound(ai_workflows_inbound_callback, wait_uuid)


@ai_workflows_public_blueprint.post('/hooks/<workflow_uuid>')
@api_doc(response_status=202, tags=['AiWorkflowsInbound'],
         summary='Start a webhook-triggered AI workflow (bearer inbound token, HMAC signature with the signing secret)')
def ai_workflows_hook_route(workflow_uuid):
    return _inbound(ai_workflows_inbound_trigger, workflow_uuid)
