#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""MCP tool: cross-object search."""
from __future__ import annotations

from app.blueprints.iris_user import iris_current_user
from app.blueprints.rest.v2.mcp import protocol
from app.blueprints.rest.v2.mcp.dispatch import MCPError
from app.blueprints.rest.v2.mcp.registry import mcp_tool
from app.business.search import SUPPORTED_SEARCH_TYPES, search_across
from app.models.authorization import Permissions


@mcp_tool(
    name='iris_search',
    description=(
        'Search across notes / iocs / comments (per `types`) for a text '
        'value, restricted to cases the caller can read. '
        f'Supported types: {sorted(SUPPORTED_SEARCH_TYPES)}.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'value': {'type': 'string', 'description': 'Text to search for.'},
            'types': {
                'type': 'array',
                'description': f'One or more of {sorted(SUPPORTED_SEARCH_TYPES)}.',
            },
            'page': {'type': 'integer'},
            'per_page': {'type': 'integer'},
            'case_id': {
                'type': 'integer',
                'description': 'Optional — restrict to a single case.',
            },
            'case_ids': {
                'type': 'array',
                'description': 'Optional — restrict to these case IDs.',
            },
        },
        'required': ['value', 'types'],
    },
    permissions=(Permissions.search_across_cases,),
    mvp=True,
)
def iris_search(args: dict) -> dict:
    search_types = args['types']
    if not isinstance(search_types, list) or not search_types:
        raise MCPError(protocol.INVALID_PARAMS,
                       "'types' must be a non-empty list.")
    unknown = [t for t in search_types if t not in SUPPORTED_SEARCH_TYPES]
    if unknown:
        raise MCPError(
            protocol.INVALID_PARAMS,
            f'Unsupported search type(s): {unknown}. '
            f'Expected one or more of {sorted(SUPPORTED_SEARCH_TYPES)}',
        )

    result = search_across(
        search_value=args['value'],
        search_types=search_types,
        user_id=iris_current_user.id,
        page=int(args.get('page') or 1),
        per_page=int(args.get('per_page') or 25),
        case_id=args.get('case_id'),
        case_ids=args.get('case_ids') or None,
    )
    return result
