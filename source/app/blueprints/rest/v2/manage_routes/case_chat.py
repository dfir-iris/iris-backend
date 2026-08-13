#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Admin case-chat endpoints.

Two surfaces live here, both `server_administrator`-gated:

  * **Egress audit** — "what did this chatbot send to a third-party
    LLM on 2026-07-27" for the DPO. Read-only, filterable by user + date.
  * **Chatbot policies** — CRUD for the per-customer `ChatbotPolicy`
    rows introduced in Stage 1. The frontend `settings/chatbot/policies`
    page drives these.
  * **Sessions viewer** — cross-user conversation listing + full-history
    reader for oversight, without leaking conversation content into the
    per-user REST tree.
"""
from __future__ import annotations

from datetime import datetime, timezone

from flask import Blueprint, request

from app.blueprints.access_controls import ac_api_requires
from app.blueprints.iris_user import iris_current_user
from app.blueprints.rest.api_doc import api_doc
from app.blueprints.rest.endpoints import (
    response_api_created,
    response_api_deleted,
    response_api_error,
    response_api_not_found,
    response_api_success,
)
from app.business import case_chat as case_chat_biz
from app.db import db
from app.iris_engine.mail.secrets import encrypt_secret
from app.iris_engine.utils.tracker import track_activity
from app.models.authorization import Permissions
from app.models.case_chat import CaseChatConversation
from app.models.chatbot_policy import ChatbotPolicy
from app.models.customers import Client
from app.schema.marshables import (
    CaseChatConversationSchema,
    CaseChatEgressAuditSchema,
    CaseChatMessageSchema,
    ChatbotPolicySchema,
)


case_chat_admin_blueprint = Blueprint(
    'case_chat_admin_rest_v2', __name__, url_prefix='/case-chat',
)

_audit_schema = CaseChatEgressAuditSchema()
_policy_schema = ChatbotPolicySchema()
_conv_schema = CaseChatConversationSchema()
_msg_schema = CaseChatMessageSchema()


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
    conv_id_raw = request.args.get('conversation_id')
    conversation_id = int(conv_id_raw) if conv_id_raw else None

    rows = case_chat_biz.list_egress_audit(
        limit=limit, offset=offset, user_id=user_id, days=days,
        conversation_id=conversation_id,
    )
    return response_api_success({
        'egress': _audit_schema.dump(rows, many=True),
        'limit': limit, 'offset': offset, 'days': days,
    })


# ---- Chatbot policies (CRUD) ---------------------------------------

@case_chat_admin_blueprint.get('/policies')
@ac_api_requires(Permissions.server_administrator)
@api_doc(
    response=ChatbotPolicySchema, tags=['ManageCaseChat'],
    summary='List every chatbot policy',
)
def list_policies():
    rows = ChatbotPolicy.query.order_by(
        ChatbotPolicy.restriction_level.desc(),
        ChatbotPolicy.name.asc(),
    ).all()
    # Attach the bound customers so the admin table can show both the
    # count and *which* customers each policy binds without a per-row
    # round-trip — the binding editor needs the ids to pre-select.
    bindings: dict[int, list[int]] = {}
    for policy_id, client_id in (
        db.session.query(Client.chatbot_policy_id, Client.client_id)
        .filter(Client.chatbot_policy_id.isnot(None))
        .order_by(Client.name.asc())
        .all()
    ):
        bindings.setdefault(int(policy_id), []).append(int(client_id))
    dumped = _policy_schema.dump(rows, many=True)
    for row, entry in zip(rows, dumped):
        bound = bindings.get(row.id, [])
        entry['customer_ids'] = bound
        entry['customer_count'] = len(bound)
    return response_api_success({'policies': dumped})


@case_chat_admin_blueprint.post('/policies')
@ac_api_requires(Permissions.server_administrator)
@api_doc(
    response=ChatbotPolicySchema, response_shape='created',
    tags=['ManageCaseChat'], summary='Create a chatbot policy',
)
def create_policy():
    body = request.get_json(silent=True) or {}
    plaintext_key = body.pop('api_key', None)
    try:
        policy = _policy_schema.load(body, session=db.session)
    except Exception as exc:
        return response_api_error(f'Invalid policy payload: {exc}')
    # Encrypt the API key before persist. Empty string / None clears
    # the field so the policy falls back to the server-settings key
    # (relevant when an admin creates a policy that only overrides
    # non-secret fields).
    if plaintext_key:
        policy.api_key = encrypt_secret(plaintext_key)
    db.session.add(policy)
    db.session.commit()
    track_activity(
        f'created chatbot policy "{policy.name}"',
        ctx_less=True, display_in_ui=False,
    )
    return response_api_created(_policy_schema.dump(policy))


@case_chat_admin_blueprint.patch('/policies/<int:policy_id>')
@ac_api_requires(Permissions.server_administrator)
@api_doc(
    response=ChatbotPolicySchema, tags=['ManageCaseChat'],
    summary='Update a chatbot policy',
)
def update_policy(policy_id: int):
    policy = ChatbotPolicy.query.filter_by(id=policy_id).first()
    if policy is None:
        return response_api_not_found()
    body = request.get_json(silent=True) or {}
    plaintext_key = body.pop('api_key', ...)  # sentinel — distinguish unset
    # Apply the partial patch field-by-field so we don't overwrite
    # unspecified fields with schema defaults (marshmallow's load with
    # partial=True is the intended tool here, but the auto-schema
    # doesn't cleanly support partial-loading of a mapped instance
    # without a fresh row; hand-patch is simpler and safer).
    _mutable_fields = {
        'name', 'description', 'restriction_level', 'provider', 'model',
        'base_url', 'auto_execute_read_tools', 'auto_approve_write_tools',
        'max_turns_per_conversation', 'max_tool_calls_per_turn',
        'daily_token_budget_per_user', 'daily_token_budget_org',
        'redact_ips', 'redact_emails', 'redact_hashes', 'retention_days',
    }
    for key, value in body.items():
        if key in _mutable_fields:
            setattr(policy, key, value)
    if plaintext_key is not ...:
        if plaintext_key:
            policy.api_key = encrypt_secret(plaintext_key)
        else:
            policy.api_key = None
    policy.updated_at = datetime.now(timezone.utc)
    db.session.commit()
    track_activity(
        f'updated chatbot policy "{policy.name}"',
        ctx_less=True, display_in_ui=False,
    )
    return response_api_success(_policy_schema.dump(policy))


@case_chat_admin_blueprint.delete('/policies/<int:policy_id>')
@ac_api_requires(Permissions.server_administrator)
@api_doc(
    response_shape='deleted', tags=['ManageCaseChat'],
    summary='Delete a chatbot policy',
)
def delete_policy(policy_id: int):
    policy = ChatbotPolicy.query.filter_by(id=policy_id).first()
    if policy is None:
        return response_api_not_found()
    name = policy.name
    # Client rows FK on ON DELETE SET NULL — customers pointing at
    # this policy quietly revert to the global default. Conversations
    # stamped with this policy id also drop to NULL (same FK behaviour)
    # but their `resolved_restriction_level` is preserved, so archive
    # decisions remain traceable.
    db.session.delete(policy)
    db.session.commit()
    track_activity(
        f'deleted chatbot policy "{name}"',
        ctx_less=True, display_in_ui=False,
    )
    return response_api_deleted()


# ---- Customer → policy binding -------------------------------------

@case_chat_admin_blueprint.put('/customers/<int:customer_id>/policy')
@ac_api_requires(Permissions.server_administrator)
@api_doc(
    tags=['ManageCaseChat'],
    summary='Bind a customer to a chatbot policy (or clear the binding)',
)
def set_customer_policy(customer_id: int):
    client = Client.query.filter_by(client_id=customer_id).first()
    if client is None:
        return response_api_not_found()
    body = request.get_json(silent=True) or {}
    policy_id = body.get('policy_id')
    if policy_id is not None:
        if not ChatbotPolicy.query.filter_by(id=policy_id).first():
            return response_api_error('Unknown chatbot policy')
        client.chatbot_policy_id = int(policy_id)
    else:
        client.chatbot_policy_id = None
    db.session.commit()
    track_activity(
        f'bound customer "{client.name}" to chatbot policy #{policy_id}'
        if policy_id else
        f'cleared chatbot policy on customer "{client.name}"',
        ctx_less=True, display_in_ui=False,
    )
    return response_api_success({
        'customer_id': customer_id,
        'policy_id': client.chatbot_policy_id,
    })


# ---- Sessions viewer (cross-user oversight) -----------------------

@case_chat_admin_blueprint.get('/sessions')
@ac_api_requires(Permissions.server_administrator)
@api_doc(
    response=CaseChatConversationSchema, tags=['ManageCaseChat'],
    summary='List every chat conversation (admin oversight)',
)
def list_sessions():
    """Paginated cross-user conversation list for the DPO/admin viewer.

    Query params: `limit` (cap 500), `offset`, `user_id`, `case_id`,
    `war_room_id`, `include_archived` (default false).
    """
    limit = min(int(request.args.get('limit') or 100), 500)
    offset = max(int(request.args.get('offset') or 0), 0)
    include_archived = request.args.get('include_archived') == 'true'

    query = CaseChatConversation.query
    if not include_archived:
        query = query.filter(CaseChatConversation.archived_at.is_(None))
    for field, column in (
        ('user_id', CaseChatConversation.user_id),
        ('case_id', CaseChatConversation.case_id),
        ('war_room_id', CaseChatConversation.war_room_id),
    ):
        raw = request.args.get(field)
        if raw:
            query = query.filter(column == int(raw))

    rows = (
        query.order_by(CaseChatConversation.updated_at.desc())
        .offset(offset).limit(limit).all()
    )
    return response_api_success({
        'conversations': _conv_schema.dump(rows, many=True),
        'limit': limit, 'offset': offset,
    })


@case_chat_admin_blueprint.get('/sessions/<int:conversation_id>')
@ac_api_requires(Permissions.server_administrator)
@api_doc(
    response=CaseChatConversationSchema, tags=['ManageCaseChat'],
    summary='Read a conversation\'s full history (admin oversight)',
)
def read_session(conversation_id: int):
    conv = CaseChatConversation.query.filter_by(id=conversation_id).first()
    if conv is None:
        return response_api_not_found()
    messages = case_chat_biz.list_messages(conversation_id)
    # Track this read — chat content is sensitive; admin access needs
    # to be visible in the activity log for compliance review.
    track_activity(
        f'admin read chat conversation #{conversation_id}',
        ctx_less=True, display_in_ui=False,
    )
    _ = iris_current_user  # touch — makes the tracker's actor lookup explicit
    payload = _conv_schema.dump(conv)
    payload['messages'] = _msg_schema.dump(messages, many=True)
    payload['usage'] = case_chat_biz.get_conversation_usage(
        conversation_id, user_id=conv.user_id)
    return response_api_success(payload)


@case_chat_admin_blueprint.get('/sessions/<int:conversation_id>/turns')
@ac_api_requires(Permissions.server_administrator)
@api_doc(
    response=CaseChatEgressAuditSchema, tags=['ManageCaseChat'],
    summary='List LLM turns for a conversation with request snapshots',
)
def read_session_turns(conversation_id: int):
    """Return every egress-audit row for one conversation, newest first.

    Each row includes `request_snapshot` — the system prompt, tools list,
    and triggering user message sent to the model on that turn, with
    conversation history stripped. Useful for verifying which tools the
    model actually received (e.g. debugging Ollama tool truncation).
    """
    from app.models.case_chat import CaseChatEgressAudit
    conv = CaseChatConversation.query.filter_by(id=conversation_id).first()
    if conv is None:
        return response_api_not_found()
    rows = (
        CaseChatEgressAudit.query
        .filter_by(conversation_id=conversation_id)
        .order_by(CaseChatEgressAudit.created_at.asc())
        .all()
    )
    track_activity(
        f'admin read turn snapshots for chat conversation #{conversation_id}',
        ctx_less=True, display_in_ui=False,
    )
    return response_api_success({
        'conversation_id': conversation_id,
        'turns': _audit_schema.dump(rows, many=True),
    })


# ---- Retention + DPO (Stage 3) -------------------------------------

@case_chat_admin_blueprint.post('/retention/run')
@ac_api_requires(Permissions.server_administrator)
@api_doc(
    tags=['ManageCaseChat'],
    summary='Run the chatbot retention purge synchronously',
)
def run_retention():
    """Trigger the retention job on demand.

    Synchronous by design — the codebase has no beat scheduler wired,
    so retention runs when an operator (or an external cron) hits this
    endpoint. Returns per-policy counts of deleted conversations,
    messages, and egress rows.
    """
    summary = case_chat_biz.run_chatbot_retention()
    track_activity(
        f'ran chatbot retention purge — deleted '
        f'{summary["conversations_deleted"]} conversation(s)',
        ctx_less=True, display_in_ui=False,
    )
    return response_api_success(summary)


@case_chat_admin_blueprint.get('/dpo/export')
@ac_api_requires(Permissions.server_administrator)
@api_doc(
    tags=['ManageCaseChat'],
    summary='Export every chat record for one user (DSAR)',
)
def dpo_export():
    user_id_raw = request.args.get('user_id')
    if not user_id_raw:
        return response_api_error('user_id is required')
    try:
        user_id = int(user_id_raw)
    except ValueError:
        return response_api_error('user_id must be an integer')
    payload = case_chat_biz.dpo_export_for_user(user_id)
    track_activity(
        f'DPO export of chat records for user #{user_id}',
        ctx_less=True, display_in_ui=False,
    )
    return response_api_success(payload)


@case_chat_admin_blueprint.post('/dpo/erase')
@ac_api_requires(Permissions.server_administrator)
@api_doc(
    tags=['ManageCaseChat'],
    summary='Hard-delete every chat record for one user (right to erasure)',
)
def dpo_erase():
    body = request.get_json(silent=True) or {}
    user_id_raw = body.get('user_id')
    if user_id_raw is None:
        return response_api_error('user_id is required')
    try:
        user_id = int(user_id_raw)
    except (ValueError, TypeError):
        return response_api_error('user_id must be an integer')
    summary = case_chat_biz.dpo_erase_user(user_id)
    track_activity(
        f'DPO erasure of chat records for user #{user_id} — '
        f'deleted {summary["conversations_deleted"]} conversation(s)',
        ctx_less=True, display_in_ui=False,
    )
    return response_api_success(summary)
