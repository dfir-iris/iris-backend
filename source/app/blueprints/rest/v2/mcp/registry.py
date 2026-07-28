#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Registry of MCP tools and resources.

Tools are declared via the `@mcp_tool` decorator on a plain callable
that takes validated arguments and returns a JSON-serialisable dict.
The dispatch layer wraps every call with auth + ACL enforcement (see
`dispatch.py`), so tool bodies stay small and only ever contain the
domain-specific glue: call `app.business.*`, marshal the result.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from app.models.authorization import Permissions


@dataclass(frozen=True)
class ToolSpec:
    """Static description of a registered MCP tool."""
    name: str
    description: str
    input_schema: dict  # JSON Schema (draft-07 subset)
    handler: Callable[[dict], Any]
    permissions: tuple[Permissions, ...] = ()
    admin_only: bool = False
    case_scoped: bool = False
    war_room_scoped: bool = False
    # Members of the default-on MVP subset. All non-MVP tools are still
    # registered so `tools/list` can advertise them, but they only appear
    # in the surface when the admin adds them to `mcp_tool_allowlist`.
    mvp: bool = False


@dataclass(frozen=True)
class ResourceSpec:
    """Static description of a registered MCP resource template."""
    uri_template: str
    name: str
    description: str
    handler: Callable[[dict], Any]
    permissions: tuple[Permissions, ...] = ()
    mime_type: str = 'application/json'


TOOL_REGISTRY: dict[str, ToolSpec] = {}
RESOURCE_REGISTRY: list[ResourceSpec] = []


def mcp_tool(
    *,
    name: str,
    description: str,
    input_schema: Optional[dict] = None,
    permissions: tuple[Permissions, ...] = (),
    admin_only: bool = False,
    case_scoped: bool = False,
    war_room_scoped: bool = False,
    mvp: bool = False,
) -> Callable:
    """Register the decorated callable as an MCP tool.

    `input_schema` defaults to an empty-object JSON Schema so tools that
    take no arguments don't need to repeat themselves. Case-scoped tools
    have `case_identifier` injected into their schema automatically by
    the dispatch layer.
    """

    def _wrap(func: Callable[[dict], Any]) -> Callable[[dict], Any]:
        schema = input_schema or {
            'type': 'object', 'properties': {}, 'additionalProperties': False,
        }
        if name in TOOL_REGISTRY:
            raise RuntimeError(f'MCP tool name collision: {name!r}')
        TOOL_REGISTRY[name] = ToolSpec(
            name=name,
            description=description,
            input_schema=schema,
            handler=func,
            permissions=tuple(permissions),
            admin_only=admin_only,
            case_scoped=case_scoped,
            war_room_scoped=war_room_scoped,
            mvp=mvp,
        )
        return func

    return _wrap


def mcp_resource(
    *,
    uri_template: str,
    name: str,
    description: str,
    permissions: tuple[Permissions, ...] = (),
    mime_type: str = 'application/json',
) -> Callable:
    """Register the decorated callable as an MCP resource template."""

    def _wrap(func: Callable[[dict], Any]) -> Callable[[dict], Any]:
        RESOURCE_REGISTRY.append(ResourceSpec(
            uri_template=uri_template,
            name=name,
            description=description,
            handler=func,
            permissions=tuple(permissions),
            mime_type=mime_type,
        ))
        return func

    return _wrap


def active_tool_names(allowlist_csv: str, denylist_csv: str) -> set[str]:
    """Return the set of tool names visible under the current settings.

    Rules:
      * Empty allowlist → MVP subset is the default surface.
      * Non-empty allowlist → union of MVP subset and the listed names,
        so an admin can widen the surface without having to re-enumerate
        the MVP tools.
      * Denylist is subtracted last and always wins.
    """
    allow_set = {n.strip() for n in allowlist_csv.split(',') if n.strip()}
    deny_set = {n.strip() for n in denylist_csv.split(',') if n.strip()}

    default_mvp = {n for n, spec in TOOL_REGISTRY.items() if spec.mvp}
    active = default_mvp | allow_set

    # Restrict to actually registered tools (silently ignore typos in the
    # allowlist to avoid a bad CSV taking the endpoint down).
    active &= set(TOOL_REGISTRY.keys())
    return active - deny_set
