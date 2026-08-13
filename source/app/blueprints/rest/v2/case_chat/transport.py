#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""REST CRUD for case-chat conversations + messages.

Non-streaming — token streaming happens on the `/chat` SocketIO
namespace. These endpoints exist so the SPA can:
  * list the user's past conversations for a case,
  * open one and read its full message history,
  * start a fresh conversation before the socket connects,
  * soft-delete a conversation.

Access control is analyst-scoped: a user only sees their own
conversations (`user_id = iris_current_user.id`). Admin doesn't get a
back-door here — auditors use the egress-audit surface for oversight.
"""
from __future__ import annotations

from flask import request

from app.blueprints.access_controls import (
    ac_api_requires,
    ac_api_return_access_denied,
    ac_fast_check_current_user_has_case_access,
    ac_fast_check_current_user_has_war_room_access,
)
from app.blueprints.iris_user import iris_current_user
from app.blueprints.rest.api_doc import api_doc
from app.blueprints.rest.endpoints import (
    response_api_created,
    response_api_deleted,
    response_api_error,
    response_api_not_found,
    response_api_success,
)
from app.blueprints.rest.v2.case_chat import case_chat_blueprint
from app.business import case_chat as case_chat_biz
from app.iris_engine.llm.client import ChatbotDisabledError, load_config
from app.models.authorization import (
    CaseAccessLevel,
    Permissions,
    WarRoomAccessLevel,
)
from app.models.case_chat import CaseChatConversation
from app.models.chatbot_policy import ChatbotPolicy
from app.models.errors import BusinessProcessingError, ObjectNotFoundError
from app.schema.marshables import (
    CaseChatConversationSchema,
    CaseChatMessageSchema,
    CaseChatPendingToolCallSchema,
)


_conv_schema = CaseChatConversationSchema()
_msg_schema = CaseChatMessageSchema()
_pending_schema = CaseChatPendingToolCallSchema()


# ---- Conversation dump + policy badge -------------------------------

def _policy_summary(policy: ChatbotPolicy | None) -> dict | None:
    """The policy facts a chatting analyst is entitled to see.

    Deliberately not the whole row: no `api_key`, and no budgets (the
    usage bar already reports those). What's left is what actually
    changes the analyst's experience — which provider/model their
    customer's data goes to, what gets scrubbed on the way out, and how
    long the transcript survives — so the SPA can say "a policy is
    enforced here" instead of leaving it invisible.
    """
    if policy is None:
        return None
    return {
        'id': policy.id,
        'name': policy.name,
        'description': policy.description or '',
        'restriction_level': int(policy.restriction_level or 0),
        'provider': policy.provider or '',
        'model': policy.model or '',
        'redact_ips': bool(policy.redact_ips),
        'redact_emails': bool(policy.redact_emails),
        'redact_hashes': bool(policy.redact_hashes),
        'retention_days': int(policy.retention_days or 0),
    }


def _dump_conversation(conv: CaseChatConversation) -> dict:
    """Serialize one conversation with its enforced policy attached."""
    payload = _conv_schema.dump(conv)
    policy = None
    if conv.resolved_policy_id is not None:
        policy = ChatbotPolicy.query.filter_by(
            id=conv.resolved_policy_id).first()
    payload['policy'] = _policy_summary(policy)
    return payload


def _dump_conversations(convs: list[CaseChatConversation]) -> list[dict]:
    """Same for a list — one policy query for the page, not one per row."""
    dumped = _conv_schema.dump(convs, many=True)
    wanted = {c.resolved_policy_id for c in convs
              if c.resolved_policy_id is not None}
    summaries: dict[int, dict] = {}
    if wanted:
        summaries = {
            p.id: _policy_summary(p)
            for p in ChatbotPolicy.query.filter(
                ChatbotPolicy.id.in_(wanted)).all()
        }
    for conv, entry in zip(convs, dumped):
        entry['policy'] = summaries.get(conv.resolved_policy_id)
    return dumped


def _require_case_access(case_id: int):
    """Belt-and-suspenders: analysts must have access to the case they're
    starting a chat about. Same enforcement as the MCP resource-read
    path — a chat on a case they can't read would let them exfiltrate
    the case via the tool-use loop.
    """
    if not ac_fast_check_current_user_has_case_access(
            case_id,
            [CaseAccessLevel.read_only, CaseAccessLevel.full_access]):
        return ac_api_return_access_denied(caseid=case_id)
    return None


def _require_war_room_access(war_room_id: int):
    """Same idea for war-room-scoped conversations. War rooms have their
    own access-level enum but the check surface mirrors the case one.
    """
    if not ac_fast_check_current_user_has_war_room_access(
            war_room_id,
            [WarRoomAccessLevel.read_only, WarRoomAccessLevel.full_access]):
        return ac_api_return_access_denied()
    return None


def _require_own_conversation(conversation_id: int):
    """Load a conversation and verify it belongs to the caller. Returns
    `(conv, None)` on success or `(None, response)` on failure so route
    handlers can early-return with the right error shape."""
    try:
        conv = case_chat_biz.get_conversation(conversation_id)
    except ObjectNotFoundError:
        return None, response_api_not_found()
    if conv.user_id != iris_current_user.id:
        return None, ac_api_return_access_denied()
    return conv, None


# ---- Case-scoped conversation CRUD ---------------------------------

@case_chat_blueprint.get('/cases/<int:case_id>/conversations')
@ac_api_requires(Permissions.standard_user)
@api_doc(
    response=CaseChatConversationSchema, tags=['CaseChat'],
    summary='List the caller\'s chat conversations for a case',
)
def list_case_conversations(case_id: int):
    err = _require_case_access(case_id)
    if err is not None:
        return err
    conversations = case_chat_biz.list_conversations_for_case(
        iris_current_user, case_id,
    )
    return response_api_success({
        'conversations': _dump_conversations(conversations),
    })


@case_chat_blueprint.post('/cases/<int:case_id>/conversations')
@ac_api_requires(Permissions.standard_user)
@api_doc(
    response=CaseChatConversationSchema, response_shape='created',
    tags=['CaseChat'],
    summary='Start a new chat conversation scoped to a case',
)
def create_case_conversation(case_id: int):
    err = _require_case_access(case_id)
    if err is not None:
        return err
    try:
        cfg = load_config()
    except Exception:
        return response_api_error('Failed to load chatbot configuration')
    if not cfg.enabled:
        return response_api_error(
            'Chatbot is disabled on this server.', status=503,
        )
    body = request.get_json(silent=True) or {}
    title = (body.get('title') or '')[:200]
    conv = case_chat_biz.create_conversation(
        user=iris_current_user._get_current_object(),  # type: ignore[attr-defined]
        case_id=case_id,
        model=cfg.model,
        title=title,
    )
    return response_api_created(_dump_conversation(conv))


# ---- War-room-scoped conversation CRUD -----------------------------

@case_chat_blueprint.get('/war-rooms/<int:war_room_id>/conversations')
@ac_api_requires(Permissions.standard_user)
@api_doc(
    response=CaseChatConversationSchema, tags=['CaseChat'],
    summary='List the caller\'s chat conversations for a war room',
)
def list_war_room_conversations(war_room_id: int):
    err = _require_war_room_access(war_room_id)
    if err is not None:
        return err
    conversations = case_chat_biz.list_conversations_for_war_room(
        iris_current_user, war_room_id,
    )
    return response_api_success({
        'conversations': _dump_conversations(conversations),
    })


@case_chat_blueprint.post('/war-rooms/<int:war_room_id>/conversations')
@ac_api_requires(Permissions.standard_user)
@api_doc(
    response=CaseChatConversationSchema, response_shape='created',
    tags=['CaseChat'],
    summary='Start a new chat conversation scoped to a war room',
)
def create_war_room_conversation(war_room_id: int):
    err = _require_war_room_access(war_room_id)
    if err is not None:
        return err
    try:
        cfg = load_config()
    except Exception:
        return response_api_error('Failed to load chatbot configuration')
    if not cfg.enabled:
        return response_api_error(
            'Chatbot is disabled on this server.', status=503,
        )
    body = request.get_json(silent=True) or {}
    title = (body.get('title') or '')[:200]
    conv = case_chat_biz.create_conversation(
        user=iris_current_user._get_current_object(),  # type: ignore[attr-defined]
        case_id=None,
        war_room_id=war_room_id,
        model=cfg.model,
        title=title,
    )
    return response_api_created(_dump_conversation(conv))


# ---- Global (no-case-scope) conversation CRUD ----------------------

@case_chat_blueprint.get('/global/conversations')
@ac_api_requires(Permissions.standard_user)
@api_doc(
    response=CaseChatConversationSchema, tags=['CaseChat'],
    summary='List the caller\'s global (no-case-scope) chat conversations',
)
def list_global_conversations():
    conversations = case_chat_biz.list_global_conversations(iris_current_user)
    return response_api_success({
        'conversations': _dump_conversations(conversations),
    })


@case_chat_blueprint.post('/global/conversations')
@ac_api_requires(Permissions.standard_user)
@api_doc(
    response=CaseChatConversationSchema, response_shape='created',
    tags=['CaseChat'],
    summary='Start a new global (no-case-scope) chat conversation',
)
def create_global_conversation():
    try:
        cfg = load_config()
    except Exception:
        return response_api_error('Failed to load chatbot configuration')
    if not cfg.enabled:
        return response_api_error(
            'Chatbot is disabled on this server.', status=503,
        )
    body = request.get_json(silent=True) or {}
    title = (body.get('title') or '')[:200]
    conv = case_chat_biz.create_conversation(
        user=iris_current_user._get_current_object(),  # type: ignore[attr-defined]
        case_id=None,
        model=cfg.model,
        title=title,
    )
    return response_api_created(_dump_conversation(conv))


# ---- Per-conversation reads ----------------------------------------

@case_chat_blueprint.get('/conversations/<int:conversation_id>')
@ac_api_requires(Permissions.standard_user)
@api_doc(
    response=CaseChatConversationSchema, tags=['CaseChat'],
    summary='Fetch a conversation with its full message history',
)
def get_conversation(conversation_id: int):
    conv, err = _require_own_conversation(conversation_id)
    if err is not None:
        return err
    # If the conversation is case- or war-room-scoped, still verify
    # access — the user may have lost access since the conversation
    # was created.
    if conv.case_id is not None:
        acl_err = _require_case_access(conv.case_id)
        if acl_err is not None:
            return acl_err
    if conv.war_room_id is not None:
        acl_err = _require_war_room_access(conv.war_room_id)
        if acl_err is not None:
            return acl_err
    messages = case_chat_biz.list_messages(conversation_id)
    payload = _dump_conversation(conv)
    payload['messages'] = _msg_schema.dump(messages, many=True)
    # Include any pending tool calls so the SPA can render the
    # Approve/Deny card even if the user closed the tab mid-turn.
    from app.models.case_chat import CaseChatPendingToolCall, STATUS_PENDING
    pending = (
        CaseChatPendingToolCall.query
        .filter_by(conversation_id=conversation_id, status=STATUS_PENDING)
        .order_by(CaseChatPendingToolCall.created_at)
        .all()
    )
    payload['pending_tool_calls'] = _pending_schema.dump(pending, many=True)
    # Aggregate token/context stats for the footer indicator. Cheap
    # (one SUM query); the socket also pushes fresh usage on every
    # assistant_end, so this GET call is only load-bearing on panel-open.
    payload['usage'] = case_chat_biz.get_conversation_usage(
        conversation_id, user_id=conv.user_id)
    return response_api_success(payload)


@case_chat_blueprint.delete('/conversations/<int:conversation_id>')
@ac_api_requires(Permissions.standard_user)
@api_doc(
    response_shape='deleted', tags=['CaseChat'],
    summary='Archive (soft-delete) a conversation',
)
def delete_conversation(conversation_id: int):
    conv, err = _require_own_conversation(conversation_id)
    if err is not None:
        return err
    try:
        case_chat_biz.archive_conversation(conv)
    except BusinessProcessingError as exc:
        return response_api_error(exc.get_message())
    return response_api_deleted()


@case_chat_blueprint.patch('/conversations/<int:conversation_id>')
@ac_api_requires(Permissions.standard_user)
@api_doc(
    response=CaseChatConversationSchema, tags=['CaseChat'],
    summary='Rename a conversation',
)
def rename_conversation(conversation_id: int):
    conv, err = _require_own_conversation(conversation_id)
    if err is not None:
        return err
    body = request.get_json(silent=True) or {}
    if 'title' not in body:
        return response_api_error('title is required')
    title = body['title']
    if not isinstance(title, str):
        return response_api_error('title must be a string')
    conv = case_chat_biz.rename_conversation(conv, title)
    return response_api_success(_dump_conversation(conv))


# ---- Configuration health probe (used by the SPA to render the
# panel's "chatbot is down" empty state).

@case_chat_blueprint.get('/health')
@ac_api_requires(Permissions.standard_user)
@api_doc(
    tags=['CaseChat'],
    summary='Return whether the chatbot is enabled and configured',
)
def health():
    try:
        cfg = load_config()
    except Exception:
        return response_api_success({
            'enabled': False, 'provider_available': False, 'reason': 'load-error',
        })
    reason: str | None = None
    provider_available = True
    try:
        from app.iris_engine.llm.client import get_llm_provider
        get_llm_provider(cfg)
    except ChatbotDisabledError as exc:
        provider_available = False
        reason = str(exc)
    return response_api_success({
        'enabled': cfg.enabled,
        'provider_available': provider_available,
        'model': cfg.model,
        'reason': reason,
    })
