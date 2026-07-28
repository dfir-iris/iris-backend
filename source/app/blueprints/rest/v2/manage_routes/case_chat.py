#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Admin egress-audit endpoint for the case-chat feature.

Answers "what did this chatbot send to a third-party LLM on
2026-07-27" for the DPO. Read-only, `server_administrator`-gated,
filterable by user id and date.
"""
from __future__ import annotations

from flask import Blueprint, request

from app.blueprints.access_controls import ac_api_requires
from app.blueprints.rest.api_doc import api_doc
from app.blueprints.rest.endpoints import response_api_success
from app.business import case_chat as case_chat_biz
from app.models.authorization import Permissions
from app.schema.marshables import CaseChatEgressAuditSchema


case_chat_admin_blueprint = Blueprint(
    'case_chat_admin_rest_v2', __name__, url_prefix='/case-chat',
)

_audit_schema = CaseChatEgressAuditSchema()


@case_chat_admin_blueprint.get('/egress-audit')
@ac_api_requires(Permissions.server_administrator)
@api_doc(
    response=CaseChatEgressAuditSchema, tags=['ManageCaseChat'],
    summary='List recent chatbot egress-audit rows',
)
def list_egress_audit():
    """Paginated list of recent LLM egress rows.

    Query params:
      * `limit`   — max rows to return (default 100, cap 1000)
      * `offset`  — pagination offset
      * `user_id` — filter to a single user (optional)
      * `days`    — window size (default 30)
    """
    limit = min(int(request.args.get('limit') or 100), 1000)
    offset = max(int(request.args.get('offset') or 0), 0)
    days = min(max(int(request.args.get('days') or 30), 1), 365)
    user_id_raw = request.args.get('user_id')
    user_id = int(user_id_raw) if user_id_raw else None

    rows = case_chat_biz.list_egress_audit(
        limit=limit, offset=offset, user_id=user_id, days=days,
    )
    return response_api_success({
        'egress': _audit_schema.dump(rows, many=True),
        'limit': limit, 'offset': offset, 'days': days,
    })
