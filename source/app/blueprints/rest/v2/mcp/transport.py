#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""MCP Streamable-HTTP JSON-RPC transport.

Single endpoint: `POST /api/v2/mcp`. JSON responses only — no SSE stream,
no session resumption. That trades away the streaming-tool-output feature
for a much smaller surface (no ASGI dependency, no session store, no
`GET /mcp` long-poll) which is worth it for a WSGI app whose tools all
return synchronously.

Auth: `ac_api_requires(Permissions.standard_user)` enforces one of IRIS's
three auth modes ran. On top of that we reject session-cookie-only callers
because MCP clients don't know IRIS's CSRF token — an API key or Bearer
token is required.

Toggle: `ServerSettings.mcp_enabled` is checked on every request. Flipping
the setting takes effect on the next request; no restart needed.

Rate limit: per-user in-process deque, only applied to `tools/call` and
`resources/read`. `initialize`, `tools/list`, `ping` and `notifications/*`
are free — they're control-plane traffic that's expensive to throttle.
"""
from __future__ import annotations

import collections
import json
import logging
import threading
import time
from typing import Any

from flask import Response, current_app, jsonify, request

from app.blueprints.access_controls import ac_api_requires
from app.blueprints.iris_user import iris_current_user
from app.blueprints.rest.v2.mcp import protocol
from app.blueprints.rest.v2.mcp import mcp_blueprint
from app.blueprints.rest.v2.mcp.dispatch import (
    MCPError,
    build_resource_templates_list,
    build_resources_list,
    build_tools_list,
    dispatch_resource_read,
    dispatch_tool_call,
)
from app.business.server_settings import get_srv_settings
from app.models.authorization import Permissions


log = logging.getLogger('iris.mcp')

# Hard cap on POST body size for MCP requests. Base64-encoded upload
# tools additionally cap their own payload at ~2 MB before decode; this
# is the outer belt.
_MAX_BODY_BYTES = 4 * 1024 * 1024

# Only `tools/call` and `resources/read` are rate-limited. `initialize`,
# `tools/list`, `resources/list`, `resources/templates/list`, `ping`,
# and `notifications/*` are control-plane traffic — throttling them
# would break normal client behaviour without any meaningful protection
# against abuse. The rate check is invoked inline where those two
# throttled methods are dispatched.


# ---- Per-user in-process rate limiter -----------------------------------

_rate_state_lock = threading.Lock()
_rate_state: dict[int, collections.deque[float]] = {}


def _check_rate_limit(user_id: int, ceiling_per_min: int) -> bool:
    """Sliding-window token bucket. Returns True if the call is allowed."""
    now = time.monotonic()
    window = 60.0
    with _rate_state_lock:
        bucket = _rate_state.setdefault(user_id, collections.deque())
        # Drop entries older than the window.
        while bucket and now - bucket[0] > window:
            bucket.popleft()
        if len(bucket) >= ceiling_per_min:
            return False
        bucket.append(now)
        return True


# ---- JSON-RPC helpers ----------------------------------------------------

def _rpc_error(request_id: Any, code: int, message: str, data: Any = None) -> dict:
    """Build a JSON-RPC 2.0 error envelope."""
    err: dict = {'code': code, 'message': message}
    if data is not None:
        err['data'] = data
    return {'jsonrpc': '2.0', 'id': request_id, 'error': err}


def _rpc_result(request_id: Any, result: Any) -> dict:
    return {'jsonrpc': '2.0', 'id': request_id, 'result': result}


def _http_json(status: int, payload: Any) -> Response:
    resp = jsonify(payload)
    resp.status_code = status
    return resp


# ---- Route ---------------------------------------------------------------

@mcp_blueprint.route('', methods=['POST'])
@mcp_blueprint.route('/', methods=['POST'])
@ac_api_requires(Permissions.standard_user)
def mcp_endpoint():
    """Single POST endpoint handling every MCP JSON-RPC method.

    Streamable-HTTP allows a JSON array of batched requests as well as
    a single request object; we accept both. `notifications/*` methods
    return no response per JSON-RPC 2.0 semantics.
    """
    # 1. Reject session-cookie-only callers. See module docstring.
    has_api_key = bool(request.headers.get('X-IRIS-AUTH'))
    has_bearer = (request.headers.get('Authorization', '').lower()
                  .startswith('bearer '))
    if not has_api_key and not has_bearer:
        return _http_json(401, _rpc_error(
            None, protocol.IRIS_UNAUTHENTICATED,
            'MCP requires an API key (X-IRIS-AUTH header) or a Bearer '
            'token. Get your API key from Profile → API Key in IRIS.',
        ))

    # 2. Toggle check.
    settings = get_srv_settings()
    if not settings.mcp_enabled:
        return _http_json(503, _rpc_error(
            None, protocol.IRIS_DISABLED,
            'MCP is disabled on this IRIS server. An administrator can '
            'enable it under Settings → MCP Server.',
        ))

    # 3. Accept header sanity check — clients typically send
    # `application/json, text/event-stream`; we don't require SSE but
    # `application/json` must be listed.
    accept = request.headers.get('Accept', '').lower()
    if accept and 'application/json' not in accept and '*/*' not in accept:
        return _http_json(406, _rpc_error(
            None, protocol.INVALID_REQUEST,
            'This server only speaks application/json; include it in Accept.',
        ))

    # 4. Body size guard — Flask already caps at MAX_CONTENT_LENGTH but
    # not every deployment sets it, so belt-and-suspenders.
    if request.content_length and request.content_length > _MAX_BODY_BYTES:
        return _http_json(413, _rpc_error(
            None, protocol.INVALID_REQUEST,
            f'Request body exceeds MCP limit of {_MAX_BODY_BYTES} bytes.',
        ))

    # 5. Parse body.
    try:
        raw = request.get_data(cache=False, as_text=True)
        payload = json.loads(raw) if raw else None
    except (ValueError, UnicodeDecodeError) as exc:
        return _http_json(400, _rpc_error(
            None, protocol.PARSE_ERROR, f'Invalid JSON: {exc}',
        ))
    if payload is None:
        return _http_json(400, _rpc_error(
            None, protocol.INVALID_REQUEST, 'Empty request body.',
        ))

    # 6. Protocol-version header validation (spec ≥2025-06-18).
    version_header = request.headers.get('MCP-Protocol-Version')
    if version_header and version_header not in protocol.SUPPORTED_PROTOCOL_VERSIONS:
        return _http_json(400, _rpc_error(
            None, protocol.INVALID_REQUEST,
            f'Unsupported MCP-Protocol-Version: {version_header!r}. '
            f'Supported: {list(protocol.SUPPORTED_PROTOCOL_VERSIONS)}',
        ))

    # 7. Dispatch — batch or single.
    if isinstance(payload, list):
        responses: list[dict] = []
        for item in payload:
            resp = _handle_one(item, settings)
            if resp is not None:
                responses.append(resp)
        # JSON-RPC batches return an array. If every request was a
        # notification (no `id`), the spec says the response body should
        # be empty — we send HTTP 202.
        if not responses:
            return Response(status=202)
        return jsonify(responses)

    resp = _handle_one(payload, settings)
    if resp is None:
        # Notification — no response body.
        return Response(status=202)
    return jsonify(resp)


@mcp_blueprint.route('', methods=['GET', 'DELETE'])
@mcp_blueprint.route('/', methods=['GET', 'DELETE'])
@ac_api_requires(Permissions.standard_user)
def mcp_endpoint_other_methods():
    """Spec-compliant 405 for GET/DELETE — we're stateless."""
    return _http_json(405, _rpc_error(
        None, protocol.METHOD_NOT_FOUND,
        'This MCP server is stateless — use POST /api/v2/mcp with a '
        'JSON-RPC request.',
    ))


# ---- Method dispatch -----------------------------------------------------

def _handle_one(item: Any, settings) -> dict | None:
    """Process a single JSON-RPC request or notification.

    Returns the response dict, or None for notifications (no reply).
    """
    if not isinstance(item, dict):
        return _rpc_error(None, protocol.INVALID_REQUEST, 'Request must be an object.')

    if item.get('jsonrpc') != '2.0':
        return _rpc_error(
            item.get('id'), protocol.INVALID_REQUEST,
            'Only JSON-RPC 2.0 is supported.',
        )

    method = item.get('method')
    request_id = item.get('id')  # may be None (notification)
    params = item.get('params') or {}
    is_notification = 'id' not in item

    if not isinstance(method, str):
        return _rpc_error(request_id, protocol.INVALID_REQUEST,
                          'Method must be a string.')

    # Notifications never get a response, per JSON-RPC 2.0.
    def _reply(payload: dict) -> dict | None:
        return None if is_notification else payload

    try:
        if method == 'initialize':
            return _reply(_rpc_result(request_id, _handle_initialize(params)))

        if method == 'ping':
            return _reply(_rpc_result(request_id, {}))

        if method == 'notifications/initialized':
            # Client → server hello; ack silently.
            return None

        if method == 'tools/list':
            return _reply(_rpc_result(request_id, {'tools': build_tools_list()}))

        if method == 'tools/call':
            if not _rate_ok(settings):
                return _reply(_rpc_error(
                    request_id, protocol.IRIS_RATE_LIMITED,
                    f'MCP rate limit exceeded '
                    f'({settings.mcp_max_calls_per_minute_per_worker}/min per worker).',
                ))
            tool_name = params.get('name')
            arguments = params.get('arguments') or {}
            if not isinstance(tool_name, str):
                return _reply(_rpc_error(
                    request_id, protocol.INVALID_PARAMS,
                    "'name' is required and must be a string.",
                ))
            result = dispatch_tool_call(tool_name, arguments)
            return _reply(_rpc_result(request_id, {
                'content': [{
                    'type': 'text',
                    'text': json.dumps(result, default=str),
                }],
            }))

        if method == 'resources/list':
            return _reply(_rpc_result(
                request_id, {'resources': build_resources_list()}))

        if method == 'resources/templates/list':
            return _reply(_rpc_result(
                request_id,
                {'resourceTemplates': build_resource_templates_list()},
            ))

        if method == 'resources/read':
            if not _rate_ok(settings):
                return _reply(_rpc_error(
                    request_id, protocol.IRIS_RATE_LIMITED,
                    f'MCP rate limit exceeded '
                    f'({settings.mcp_max_calls_per_minute_per_worker}/min per worker).',
                ))
            uri = params.get('uri')
            if not isinstance(uri, str):
                return _reply(_rpc_error(
                    request_id, protocol.INVALID_PARAMS,
                    "'uri' is required and must be a string.",
                ))
            mime, payload = dispatch_resource_read(uri)
            return _reply(_rpc_result(request_id, {
                'contents': [{
                    'uri': uri,
                    'mimeType': mime,
                    'text': json.dumps(payload, default=str),
                }],
            }))

        return _reply(_rpc_error(
            request_id, protocol.METHOD_NOT_FOUND,
            f'Unknown method: {method!r}',
        ))

    except MCPError as exc:
        return _reply(_rpc_error(request_id, exc.code, exc.message, exc.data))
    except Exception as exc:
        log.exception('MCP internal error')
        return _reply(_rpc_error(
            request_id, protocol.INTERNAL_ERROR, f'Internal error: {exc}',
        ))


def _handle_initialize(params: dict) -> dict:
    """MCP handshake. Echo the client's version if supported."""
    requested = params.get('protocolVersion')
    if requested in protocol.SUPPORTED_PROTOCOL_VERSIONS:
        version = requested
    else:
        version = protocol.LATEST_PROTOCOL_VERSION

    return {
        'protocolVersion': version,
        'capabilities': {
            'tools': {},       # no `listChanged` — stateless server.
            'resources': {},
        },
        'serverInfo': {
            'name': 'iris',
            'title': 'IRIS DFIR',
            'version': str(current_app.config.get('IRIS_VERSION') or 'unknown'),
        },
    }


def _rate_ok(settings) -> bool:
    ceiling = int(settings.mcp_max_calls_per_minute_per_worker or 60)
    user_id = getattr(iris_current_user, 'id', None)
    if user_id is None:
        return True  # unreachable — ac_api_requires already ran.
    return _check_rate_limit(user_id, ceiling)
