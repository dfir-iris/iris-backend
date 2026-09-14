#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Shared helpers for MCP tool wrappers.

Tools all follow the same shape: turn `arguments` into a validated
domain object (or plain kwargs), call `app.business.<domain>.<action>`,
and marshal the return via a Marshmallow schema. The helpers below
factor out pagination, response projection and the schema-driven
load/dump pattern so the per-domain modules stay focused on the
domain-specific mapping.
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


def dump_paginated(schema, pagination, previews: dict[str, int] | None = None) -> dict:
    """Shape a flask-sqlalchemy Pagination-like object for the wire.

    `previews` clips the named free-text fields of every row — see
    `clip_text_field`. Used by list tools whose rows carry a
    human-written body (case descriptions, note contents) that is worth
    a few lines of context but not its full length times a page of rows.
    """
    rows = schema.dump(pagination.items, many=True)
    for field, limit in (previews or {}).items():
        clip_text_field(rows, field, limit)
    return {
        'total': pagination.total,
        'data': rows,
        'last_page': pagination.pages,
        'current_page': pagination.page,
        'next_page': pagination.next_num if pagination.has_next else None,
    }


# ---- Response views ------------------------------------------------
#
# List tools return a lean projection by default and let the caller ask
# for everything with `view=full`. The default is what bounds context:
# the auto-schemas behind these tools carry `include_relationships`, so
# an unprojected row drags in every nested collection (an alert pulls
# its IOCs, assets, customer, classification and owner in full) plus the
# raw detection payload and the whole modification history. Multiplied
# by a page of rows that is tens of thousands of tokens for one call.
#
# The projection is a default, not a restriction: every field remains
# reachable through `view=full` or the matching `*_get` tool, which is
# what keeps this from quietly removing capability.

VIEW_SUMMARY = 'summary'
VIEW_FULL = 'full'

VIEW_SCHEMA_FRAGMENT: dict[str, Any] = {
    'view': {
        'type': 'string',
        'enum': [VIEW_SUMMARY, VIEW_FULL],
        'description': (
            f"Response detail level. '{VIEW_SUMMARY}' (the default) returns "
            'the identifying, triage and cross-reference fields. '
            f"'{VIEW_FULL}' adds raw source payloads, enrichment blobs, "
            'custom attributes and modification history, and is typically '
            'an order of magnitude larger. Stay on the default unless a '
            'field you need is genuinely absent — to read one record in '
            'full, prefer the matching `*_get` tool over widening a whole '
            'page of results.'
        ),
    },
}


def requested_view(args: dict) -> str:
    """Return the view the caller asked for, defaulting to summary.

    Anything that isn't exactly `full` is treated as summary so a typo
    or a hallucinated value degrades to the cheap projection instead of
    the expensive one.
    """
    return VIEW_FULL if args.get('view') == VIEW_FULL else VIEW_SUMMARY


def schema_for_view(args: dict, summary_schema, full_schema):
    """Pick between the two pre-built projections of a schema."""
    return full_schema if requested_view(args) == VIEW_FULL else summary_schema


def clip_text_field(rows: list[dict], field: str, limit: int) -> None:
    """Clip `field` of every row to `limit` characters, in place.

    The marker is left in the value rather than in a sibling key so the
    model cannot read the preview as if it were the whole field — an
    elided closing note that looks complete is worse than an obviously
    truncated one.
    """
    for row in rows:
        value = row.get(field)
        if isinstance(value, str) and len(value) > limit:
            row[field] = (
                f'{value[:limit]}… [clipped, {len(value)} chars total]'
            )


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
