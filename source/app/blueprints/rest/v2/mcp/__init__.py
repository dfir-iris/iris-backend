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

"""MCP (Model Context Protocol) endpoint mounted under /api/v2/mcp.

Exposes IRIS's v2 REST surface as MCP tools + resources so AI clients
(Claude Desktop, Claude Code, ...) can drive incident-response workflows
on behalf of an analyst. The blueprint is registered unconditionally;
`ServerSettings.mcp_enabled` gates every request at the transport layer
so admins can toggle without a restart.

Structure:
  - protocol.py: vendored wire constants (protocol versions, JSON-RPC
    error codes). No dependency on the `mcp` PyPI SDK — the wire is
    small and the SDK is ASGI-first, we're WSGI.
  - registry.py: `@mcp_tool` / `@mcp_resource` decorators + spec dataclass.
  - dispatch.py: permission/ACL enforcement + tool invocation.
  - transport.py: Streamable-HTTP JSON-RPC endpoint (single POST route).
  - resources.py: read-only URI-addressable views over cases/alerts/me.
  - tools/*: one module per domain, MVP set on by default, extended set
    opt-in via `ServerSettings.mcp_tool_allowlist`.

The tool modules are imported at package import time so their decorator
side-effects register into `TOOL_REGISTRY`. Import order does not matter
across modules since each decorator writes to a shared dict.
"""

from flask import Blueprint

mcp_blueprint = Blueprint('rest_v2_mcp', __name__, url_prefix='/mcp')

# ruff: noqa: F401
# Import order below is intentional: `transport` must import first so
# the route is attached to the blueprint before any tool module gets a
# chance to call helpers that require the app context. Tool modules are
# imported for their decorator side-effects (they register into
# TOOL_REGISTRY).
from app.blueprints.rest.v2.mcp import transport as _transport
from app.blueprints.rest.v2.mcp import resources as _resources
from app.blueprints.rest.v2.mcp.tools import cases as _tools_cases
from app.blueprints.rest.v2.mcp.tools import alerts as _tools_alerts
from app.blueprints.rest.v2.mcp.tools import iocs as _tools_iocs
from app.blueprints.rest.v2.mcp.tools import assets as _tools_assets
from app.blueprints.rest.v2.mcp.tools import notes as _tools_notes
from app.blueprints.rest.v2.mcp.tools import tasks as _tools_tasks
from app.blueprints.rest.v2.mcp.tools import search as _tools_search
from app.blueprints.rest.v2.mcp.tools import profile as _tools_profile
from app.blueprints.rest.v2.mcp.tools import war_rooms as _tools_war_rooms
