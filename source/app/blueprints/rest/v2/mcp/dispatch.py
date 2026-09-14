#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""MCP tool + resource dispatch: permission and ACL enforcement.

Every tool call and every resource read funnels through here. The
transport layer has already established authentication (`ac_api_requires`)
so `iris_current_user` is populated; the checks below are the fine-grained
per-tool authorization. All denials raise `MCPError` with a well-known
JSON-RPC error code that `transport.py` converts to the wire response.

A scoped tool is held to the same access level as the REST route that
does the same thing: reads need `read_only` on the case / war room,
writes need `full_access` (see `required_case_levels`). Going through an
LLM must not be a way around what the UI would refuse.
"""
from __future__ import annotations

import logging
from typing import Any

from app.blueprints.access_controls import (
    _user_has_at_least_a_required_permission,
    ac_fast_check_current_user_has_case_access,
    ac_fast_check_current_user_has_war_room_access,
)
from app.blueprints.iris_user import iris_current_user
from app.blueprints.rest.v2.mcp import protocol
from app.blueprints.rest.v2.mcp.classification import mutates
from app.blueprints.rest.v2.mcp.registry import (
    RESOURCE_REGISTRY,
    TOOL_REGISTRY,
    ToolSpec,
    active_tool_names,
)
from app.blueprints.rest.v2.mcp.result_budget import apply_result_budget
from app.business.server_settings import get_srv_settings
from app.iris_engine.utils.tracker import track_activity
from app.models.authorization import CaseAccessLevel, Permissions, WarRoomAccessLevel


log = logging.getLogger('iris.mcp')


class MCPError(Exception):
    """Raised by dispatch when a tool call must be rejected.

    The transport layer catches this and renders a JSON-RPC error using
    `code` and `message`; `data` is optional structured detail.
    """

    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


# ---- Tool listing --------------------------------------------------------

def build_tools_list() -> list[dict]:
    """Return the tool descriptors visible under the current settings.

    Called from `tools/list`. Admin-only tools are further filtered by
    caller privilege so a non-admin never sees `iris_manage_*` in their
    tool list even if the setting exposes them.
    """
    settings = get_srv_settings()
    active = active_tool_names(
        settings.mcp_tool_allowlist or '',
        settings.mcp_tool_denylist or '',
    )

    expose_admin = bool(settings.mcp_expose_admin_tools)
    caller_is_admin = _user_has_at_least_a_required_permission(
        [Permissions.server_administrator]
    )

    out: list[dict] = []
    for tool_name in sorted(active):
        spec = TOOL_REGISTRY[tool_name]
        if spec.admin_only and not (expose_admin and caller_is_admin):
            continue
        out.append({
            'name': spec.name,
            'description': spec.description,
            'inputSchema': _augmented_input_schema(spec),
        })
    return out


def _augmented_input_schema(spec: ToolSpec) -> dict:
    """Return the tool's declared input schema plus injected scoping fields.

    Case-scoped and war-room-scoped tools always take an `_identifier`
    argument that the caller must supply; injecting it here keeps the
    per-tool schema declaration focused on the domain-specific args.
    """
    schema = dict(spec.input_schema)
    props = dict(schema.get('properties', {}))
    required = list(schema.get('required', []))

    if spec.case_scoped and 'case_identifier' not in props:
        props['case_identifier'] = {
            'type': 'integer',
            'description': 'Numeric ID of the IRIS case this call targets.',
        }
        if 'case_identifier' not in required:
            required.append('case_identifier')

    if spec.war_room_scoped and 'war_room_id' not in props:
        props['war_room_id'] = {
            'type': 'integer',
            'description': 'Numeric ID of the war room this call targets.',
        }
        if 'war_room_id' not in required:
            required.append('war_room_id')

    schema['properties'] = props
    if required:
        schema['required'] = required
    return schema


# ---- Scoped access levels ------------------------------------------------

def required_case_levels(tool_name: str) -> list[CaseAccessLevel]:
    """Case access levels that let `tool_name` run.

    A mutating tool demands `full_access`, exactly like the REST route
    doing the same thing — the business layer does NOT re-check it
    (`notes_create`, `tasks_create` & co. write unconditionally), so this
    is the only thing standing between a read-only case member and a
    mutation. Reads accept `read_only`. `mutates()` is fail-closed: a
    tool nobody classified is treated as a write.
    """
    if mutates(tool_name):
        return [CaseAccessLevel.full_access]
    return [CaseAccessLevel.read_only, CaseAccessLevel.full_access]


def required_war_room_levels(tool_name: str) -> list[WarRoomAccessLevel]:
    """War-room equivalent of `required_case_levels` — mirrors
    `require_war_room_read` / `require_war_room_write`."""
    if mutates(tool_name):
        return [WarRoomAccessLevel.full_access]
    return [WarRoomAccessLevel.read_only, WarRoomAccessLevel.full_access]


# ---- Tool call -----------------------------------------------------------

def dispatch_tool_call(tool_name: str, raw_args: dict) -> Any:
    """Validate + authorize + execute one tool call.

    Returns the tool's raw result dict; the transport wraps it into the
    `content: [{type: "text", text: ...}]` envelope on the wire.
    """
    settings = get_srv_settings()
    active = active_tool_names(
        settings.mcp_tool_allowlist or '',
        settings.mcp_tool_denylist or '',
    )
    if tool_name not in active:
        raise MCPError(
            protocol.METHOD_NOT_FOUND,
            f'Unknown or disabled tool: {tool_name!r}',
        )

    spec = TOOL_REGISTRY[tool_name]

    # Per-user opt-out. Checked before permissions so a disabled user
    # gets the same denial regardless of what tool they call.
    if not getattr(iris_current_user, 'mcp_allowed', True):
        raise MCPError(
            protocol.IRIS_ACCESS_DENIED,
            'MCP is disabled for this user account.',
        )

    if spec.admin_only:
        if not settings.mcp_expose_admin_tools:
            raise MCPError(
                protocol.IRIS_ACCESS_DENIED,
                'Administrative MCP tools are disabled by server settings.',
            )
        if not _user_has_at_least_a_required_permission(
                [Permissions.server_administrator]):
            raise MCPError(
                protocol.IRIS_ACCESS_DENIED,
                'Server-administrator permission required.',
            )

    if spec.permissions and not _user_has_at_least_a_required_permission(
            list(spec.permissions)):
        raise MCPError(
            protocol.IRIS_ACCESS_DENIED,
            'Missing required permission for tool.',
        )

    args = raw_args if isinstance(raw_args, dict) else {}

    # Validate against the augmented schema (includes injected scoping
    # fields) so an omitted `case_identifier` is a clear INVALID_PARAMS
    # rather than an obscure KeyError later. We deliberately implement a
    # tiny inline validator (see `_validate_args`) instead of adding a
    # `jsonschema` dependency — the schemas we ship are small and only
    # exercise required/type/properties.
    error = _validate_args(args, _augmented_input_schema(spec))
    if error:
        raise MCPError(protocol.INVALID_PARAMS, f'Invalid arguments: {error}')

    is_mutating = mutates(tool_name)

    if spec.case_scoped:
        case_id = args.get('case_identifier')
        if not ac_fast_check_current_user_has_case_access(
                case_id, required_case_levels(tool_name)):
            denial = (f'Full access to case #{case_id} is required to run '
                      f'{tool_name!r}.') if is_mutating else \
                     f'No access to case #{case_id}.'
            raise MCPError(protocol.IRIS_ACCESS_DENIED, denial)

    if spec.war_room_scoped:
        war_room_id = args.get('war_room_id')
        if not ac_fast_check_current_user_has_war_room_access(
                war_room_id, required_war_room_levels(tool_name)):
            denial = (f'Full access to war room #{war_room_id} is required to '
                      f'run {tool_name!r}.') if is_mutating else \
                     f'No access to war room #{war_room_id}.'
            raise MCPError(protocol.IRIS_ACCESS_DENIED, denial)

    try:
        result = spec.handler(args)
    except MCPError:
        raise
    except Exception as exc:
        log.exception('MCP tool %s failed', tool_name)
        raise MCPError(
            protocol.INTERNAL_ERROR,
            f'Tool {tool_name!r} raised: {exc}',
        ) from exc

    # Bound the payload before it reaches the caller. Tool bodies choose
    # their own projections; this is the floor that applies even to the
    # ones that don't, and to any `view=full` request.
    result, truncation = apply_result_budget(result)
    if truncation is not None:
        log.info('MCP tool %s result truncated: %s', tool_name, truncation)

    # Audit only successful mutating/read calls. `tools/list` and
    # `initialize` are called on every session start and would flood the
    # activity log.
    perm_label = spec.permissions[0].name if spec.permissions else 'none'
    caseid = args.get('case_identifier') if spec.case_scoped else None
    track_activity(
        f'mcp:{tool_name} perm={perm_label}',
        caseid=caseid,
        ctx_less=caseid is None,
        display_in_ui=caseid is not None,
    )
    return result


# ---- Resource reads ------------------------------------------------------

def build_resources_list() -> list[dict]:
    """Return every registered resource template as an MCP resource entry."""
    out: list[dict] = []
    for res in RESOURCE_REGISTRY:
        out.append({
            'uri': res.uri_template,
            'name': res.name,
            'description': res.description,
            'mimeType': res.mime_type,
        })
    return out


def build_resource_templates_list() -> list[dict]:
    """Same as `build_resources_list` but shaped for `resources/templates/list`."""
    out: list[dict] = []
    for res in RESOURCE_REGISTRY:
        out.append({
            'uriTemplate': res.uri_template,
            'name': res.name,
            'description': res.description,
            'mimeType': res.mime_type,
        })
    return out


def dispatch_resource_read(uri: str) -> tuple[str, Any]:
    """Resolve `uri` against the registered templates and read the resource.

    Returns `(mime_type, payload)`; `transport.py` wraps that into the
    `contents: [{uri, mimeType, text}]` envelope.
    """
    for res in RESOURCE_REGISTRY:
        params = _match_uri(res.uri_template, uri)
        if params is None:
            continue
        if res.permissions and not _user_has_at_least_a_required_permission(
                list(res.permissions)):
            raise MCPError(
                protocol.IRIS_ACCESS_DENIED,
                'Missing required permission for resource.',
            )
        try:
            payload = res.handler(params)
        except MCPError:
            raise
        except Exception as exc:
            log.exception('MCP resource %s failed', uri)
            raise MCPError(
                protocol.INTERNAL_ERROR,
                f'Resource read failed: {exc}',
            ) from exc
        return res.mime_type, payload

    raise MCPError(protocol.METHOD_NOT_FOUND, f'Unknown resource URI: {uri!r}')


_JSON_TYPE_MAP = {
    'string': str,
    'integer': int,
    'boolean': bool,
    'number': (int, float),
    'object': dict,
    'array': list,
}


def _validate_args(args: dict, schema: dict) -> str | None:
    """Tiny JSON-Schema subset validator: type / required / properties.

    Returns None on success, a human-readable error message on failure.
    Only the features actually used by our tool schemas are enforced —
    additional keys are ignored (schema `additionalProperties: false`
    is intentionally NOT enforced so tools can accept forward-compatible
    keys without immediately breaking older clients).
    """
    if schema.get('type') and schema['type'] != 'object':
        return None  # non-object top-level unused today

    if not isinstance(args, dict):
        return 'expected object'

    for key in schema.get('required', []):
        if key not in args:
            return f'missing required property {key!r}'

    for key, value in args.items():
        prop = schema.get('properties', {}).get(key)
        if not prop:
            continue
        wanted = prop.get('type')
        if not wanted:
            continue
        py_type = _JSON_TYPE_MAP.get(wanted)
        if py_type is None:
            continue
        # JSON booleans are also `int` in Python — reject that overlap
        # explicitly so a stray `True` doesn't slip through an `integer`
        # slot.
        if wanted == 'integer' and isinstance(value, bool):
            return f'{key!r}: expected integer, got boolean'
        if not isinstance(value, py_type):
            return f'{key!r}: expected {wanted}, got {type(value).__name__}'
    return None


def _match_uri(template: str, uri: str) -> dict[str, str] | None:
    """Naive `{name}` placeholder matcher — enough for our small template set.

    Returns a `{param: value}` dict when `uri` matches `template`, else
    None. We intentionally do NOT depend on `uritemplate` — the template
    grammar we use is a strict subset (single-segment `{name}` only) and
    hand-parsing keeps the dep footprint clean.
    """
    if '{' not in template:
        return {} if template == uri else None

    template_parts = template.split('/')
    uri_parts = uri.split('/')
    if len(template_parts) != len(uri_parts):
        return None

    out: dict[str, str] = {}
    for t_seg, u_seg in zip(template_parts, uri_parts):
        if t_seg.startswith('{') and t_seg.endswith('}'):
            out[t_seg[1:-1]] = u_seg
        elif t_seg != u_seg:
            return None
    return out
