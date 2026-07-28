#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Shared helpers for MCP tool wrappers.

Tools all follow the same shape: turn `arguments` into a validated
domain object (or plain kwargs), call `app.business.<domain>.<action>`,
and marshal the return via a Marshmallow schema. The helpers below
factor out pagination and the schema-driven load/dump pattern so the
per-domain modules stay focused on the domain-specific mapping.
"""
from __future__ import annotations

from typing import Any

from app.models.pagination_parameters import PaginationParameters

# Hard cap on `per_page` for any list-style tool. Prevents an LLM from
# accidentally asking for the whole DB and blowing its own context; also
# keeps single-response payloads bounded independent of the outer
# `MAX_CONTENT_LENGTH`.
MAX_PER_PAGE = 100
DEFAULT_PER_PAGE = 25


def build_pagination(args: dict) -> PaginationParameters:
    """Extract `page`, `per_page`, `order_by`, `sort_dir` from tool args."""
    page = int(args.get('page') or 1)
    per_page = int(args.get('per_page') or DEFAULT_PER_PAGE)
    if per_page > MAX_PER_PAGE:
        per_page = MAX_PER_PAGE
    order_by = args.get('order_by')
    direction = args.get('sort_dir', 'asc')
    return PaginationParameters(page, per_page, order_by, direction)


def dump_paginated(schema, pagination) -> dict:
    """Shape a flask-sqlalchemy Pagination-like object for the wire."""
    return {
        'total': pagination.total,
        'data': schema.dump(pagination.items, many=True),
        'last_page': pagination.pages,
        'current_page': pagination.page,
        'next_page': pagination.next_num if pagination.has_next else None,
    }


# JSON-Schema fragment shared by every list tool. Merged into the tool's
# input_schema declaration at registration time.
PAGINATION_SCHEMA_FRAGMENT: dict[str, Any] = {
    'page': {'type': 'integer', 'description': '1-indexed page number.'},
    'per_page': {
        'type': 'integer',
        'description': f'Items per page (default {DEFAULT_PER_PAGE}, max {MAX_PER_PAGE}).',
    },
    'order_by': {'type': 'string', 'description': 'Field name to sort by.'},
    'sort_dir': {
        'type': 'string',
        'description': "'asc' or 'desc'.",
    },
}
