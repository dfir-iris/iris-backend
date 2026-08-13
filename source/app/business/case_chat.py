#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Business helpers for the case-chat feature.

Persistence CRUD + budget summariser + pending-tool-call resolution.
Kept off `app.blueprints` / Flask imports per the import-linter
contract, so the case_chat REST layer can call these directly without
pulling the API layer back into itself.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.db import db
from app.models.authorization import User
from app.models.case_chat import (
    STATUS_APPROVED,
    STATUS_DENIED,
    STATUS_PENDING,
    CaseChatConversation,
    CaseChatEgressAudit,
    CaseChatMessage,
    CaseChatPendingToolCall,
)
from app.models.errors import BusinessProcessingError, ObjectNotFoundError


# ---- Conversations ---------------------------------------------------

def create_conversation(
    *,
    user: User,
    case_id: Optional[int],
    model: str,
    title: str = '',
    war_room_id: Optional[int] = None,
) -> CaseChatConversation:
    # A conversation is scoped to at most one of (case, war-room). The
    # SPA sets exactly one based on the route; global is "neither".
    # We don't enforce mutual exclusion with a check constraint — cheap
    # to relax later if we ever want to link both.
    #
    # Stamp the resolved policy at creation. `model` here is the model
    # NAME chosen by the caller, read from the global server settings.
    from app.iris_engine.llm.policy import (
        resolve_for_case,
        resolve_for_war_room,
    )
    if war_room_id is not None:
        resolved = resolve_for_war_room(war_room_id)
    elif case_id is not None:
        resolved = resolve_for_case(case_id)
    else:
        from app.iris_engine.llm.policy import ResolvedPolicy
        resolved = ResolvedPolicy(None, 0)

    # The stored model is what the chat header reports, so it has to be
    # the model the loop will actually call — a policy that pins its own
    # model overrides the global one the caller passed in. Same
    # precedence as `load_config`'s `_pick`: empty string means the
    # policy doesn't override, so the global value stands.
    effective_model = model
    if resolved.policy is not None and resolved.policy.model:
        effective_model = resolved.policy.model

    conv = CaseChatConversation(
        user_id=user.id,
        case_id=case_id,
        war_room_id=war_room_id,
        model=effective_model,
        title=(title or '')[:200],
        resolved_policy_id=resolved.policy.id if resolved.policy else None,
        resolved_restriction_level=resolved.restriction_level,
    )
    db.session.add(conv)
    db.session.commit()
    return conv


def get_conversation(conversation_id: int) -> CaseChatConversation:
    conv = CaseChatConversation.query.filter_by(id=conversation_id).first()
    if conv is None:
        raise ObjectNotFoundError()
    return conv


def list_conversations_for_case(
    user: User, case_id: int, include_archived: bool = False,
) -> list[CaseChatConversation]:
    query = CaseChatConversation.query.filter_by(
        user_id=user.id, case_id=case_id,
    )
    if not include_archived:
        query = query.filter(CaseChatConversation.archived_at.is_(None))
    return query.order_by(CaseChatConversation.updated_at.desc()).all()


def list_global_conversations(
    user: User, include_archived: bool = False,
) -> list[CaseChatConversation]:
    """Global-scope conversations — no case AND no war-room."""
    query = CaseChatConversation.query.filter(
        CaseChatConversation.user_id == user.id,
        CaseChatConversation.case_id.is_(None),
        CaseChatConversation.war_room_id.is_(None),
    )
    if not include_archived:
        query = query.filter(CaseChatConversation.archived_at.is_(None))
    return query.order_by(CaseChatConversation.updated_at.desc()).all()


def list_conversations_for_war_room(
    user: User, war_room_id: int, include_archived: bool = False,
) -> list[CaseChatConversation]:
    query = CaseChatConversation.query.filter_by(
        user_id=user.id, war_room_id=war_room_id,
    )
    if not include_archived:
        query = query.filter(CaseChatConversation.archived_at.is_(None))
    return query.order_by(CaseChatConversation.updated_at.desc()).all()


def archive_conversation(conv: CaseChatConversation) -> CaseChatConversation:
    if conv.archived_at is None:
        conv.archived_at = datetime.now(timezone.utc)
        db.session.commit()
    return conv


def rename_conversation(
    conv: CaseChatConversation, title: str,
) -> CaseChatConversation:
    """Update the human-readable title. Truncates to the column's 200-char
    limit; empty titles are permitted (the SPA falls back to the first
    user turn's opening words when rendering the history list)."""
    conv.title = (title or '')[:200]
    conv.updated_at = datetime.now(timezone.utc)
    db.session.commit()
    return conv


# ---- Messages --------------------------------------------------------

def list_messages(conversation_id: int) -> list[CaseChatMessage]:
    return (
        CaseChatMessage.query
        .filter_by(conversation_id=conversation_id)
        .order_by(CaseChatMessage.created_at, CaseChatMessage.id)
        .all()
    )


def append_message(
    *,
    conversation: CaseChatConversation,
    role: str,
    content: list[dict[str, Any]],
    tool_use_id: Optional[str] = None,
) -> CaseChatMessage:
    msg = CaseChatMessage(
        conversation_id=conversation.id,
        role=role,
        content=content,
        tool_use_id=tool_use_id,
    )
    db.session.add(msg)
    conversation.updated_at = datetime.now(timezone.utc)
    db.session.commit()
    return msg


# ---- Pending tool calls ---------------------------------------------

def create_pending_tool_call(
    *,
    conversation: CaseChatConversation,
    assistant_message: CaseChatMessage,
    tool_use_id: str,
    tool_name: str,
    arguments: dict[str, Any],
) -> CaseChatPendingToolCall:
    row = CaseChatPendingToolCall(
        conversation_id=conversation.id,
        assistant_message_id=assistant_message.id,
        tool_use_id=tool_use_id,
        tool_name=tool_name,
        arguments=arguments,
        status=STATUS_PENDING,
    )
    db.session.add(row)
    db.session.commit()
    return row


def resolve_pending_tool_call(
    *,
    pending_id: int,
    user: User,
    approve: bool,
) -> tuple[CaseChatPendingToolCall, bool]:
    """Mark a pending tool call approved or denied — with row-level lock.

    Uses `SELECT ... FOR UPDATE` so a double-click on Approve can't
    dispatch the tool twice. Returns `(row, transitioned)` where
    `transitioned` is True only for the caller that flipped the row
    from pending — subsequent callers (double-click, retry, second
    tab) get `(row, False)` and MUST NOT dispatch again, or the
    `tool_result` history gets duplicated and Anthropic rejects the
    next request with "each tool_use must have a single result".
    """
    row = (
        CaseChatPendingToolCall.query
        .filter_by(id=pending_id)
        .with_for_update()
        .first()
    )
    if row is None:
        raise ObjectNotFoundError()
    conv = get_conversation(row.conversation_id)
    if conv.user_id != user.id:
        raise BusinessProcessingError(
            'This pending tool call belongs to another user.'
        )
    if row.status != STATUS_PENDING:
        return row, False
    row.status = STATUS_APPROVED if approve else STATUS_DENIED
    row.resolved_at = datetime.now(timezone.utc)
    row.resolved_by_user_id = user.id
    db.session.commit()
    return row, True


def count_pending_tool_calls(
    conversation_id: int, assistant_message_id: int,
) -> int:
    return (
        CaseChatPendingToolCall.query
        .filter_by(
            conversation_id=conversation_id,
            assistant_message_id=assistant_message_id,
            status=STATUS_PENDING,
        )
        .count()
    )


# ---- Egress audit + budget ------------------------------------------

def record_egress(
    *,
    conversation: CaseChatConversation,
    user: User,
    provider: str,
    model: str,
    request_bytes: int,
    redacted: bool,
    request_snapshot: Optional[dict] = None,
) -> CaseChatEgressAudit:
    row = CaseChatEgressAudit(
        conversation_id=conversation.id,
        user_id=user.id,
        provider=provider,
        model=model,
        request_bytes=int(request_bytes),
        response_bytes=0,
        redacted=redacted,
        request_snapshot=request_snapshot,
    )
    db.session.add(row)
    db.session.commit()
    return row


def finalise_egress(
    *,
    audit_row: CaseChatEgressAudit,
    response_bytes: int,
    prompt_tokens: Optional[int],
    completion_tokens: Optional[int],
    cache_read_tokens: Optional[int] = None,
    cache_creation_tokens: Optional[int] = None,
) -> CaseChatEgressAudit:
    audit_row.response_bytes = int(response_bytes)
    if prompt_tokens is not None:
        audit_row.prompt_tokens = int(prompt_tokens)
    if completion_tokens is not None:
        audit_row.completion_tokens = int(completion_tokens)
    if cache_read_tokens is not None:
        audit_row.cache_read_tokens = int(cache_read_tokens)
    if cache_creation_tokens is not None:
        audit_row.cache_creation_tokens = int(cache_creation_tokens)
    db.session.commit()
    return audit_row


def get_conversation_usage(
    conversation_id: int,
    *,
    user_id: Optional[int] = None,
) -> dict[str, Any]:
    """Aggregate token/context stats for one conversation's egress rows.

    `avg_context_size` and `last_context_size` are computed from
    `prompt_tokens` because that column IS the size of the payload sent
    to the provider on each turn (system + history + user turn). It
    trends up as the conversation grows, so surfacing it helps analysts
    spot when a thread is getting expensive to continue.

    When `user_id` is passed, the payload also includes today's per-user
    and org-wide token spend + the configured daily budgets, so the
    usage-bar renderer has everything it needs in one round-trip. Kept
    optional so admin / batch callers don't pay for the budget lookup.

    All sums coalesce NULL → 0 so pre-migration egress rows (which have
    NULL cache columns) don't poison the totals.
    """
    coalesce = db.func.coalesce
    prompt_sum = coalesce(db.func.sum(CaseChatEgressAudit.prompt_tokens), 0)
    completion_sum = coalesce(
        db.func.sum(CaseChatEgressAudit.completion_tokens), 0)
    cache_read_sum = coalesce(
        db.func.sum(CaseChatEgressAudit.cache_read_tokens), 0)
    cache_creation_sum = coalesce(
        db.func.sum(CaseChatEgressAudit.cache_creation_tokens), 0)
    avg_prompt = db.func.avg(CaseChatEgressAudit.prompt_tokens)
    turn_count = db.func.count(CaseChatEgressAudit.id)
    row = db.session.query(
        prompt_sum, completion_sum, cache_read_sum, cache_creation_sum,
        avg_prompt, turn_count,
    ).filter(
        CaseChatEgressAudit.conversation_id == conversation_id,
    ).one()
    prompt_total = int(row[0] or 0)
    completion_total = int(row[1] or 0)
    cache_read_total = int(row[2] or 0)
    cache_creation_total = int(row[3] or 0)
    # `AVG` returns Decimal on Postgres — `int()` truncates to whole
    # tokens, which is the useful resolution for a UI counter.
    avg_context = int(row[4]) if row[4] is not None else 0
    turns = int(row[5] or 0)

    # Last-turn context size — cheap follow-up query so the UI can show
    # "current" alongside "average" without recomputing client-side.
    last_context_row = (
        db.session.query(CaseChatEgressAudit.prompt_tokens)
        .filter(CaseChatEgressAudit.conversation_id == conversation_id)
        .order_by(CaseChatEgressAudit.id.desc())
        .first()
    )
    last_context_size = int(last_context_row[0] or 0) if last_context_row else 0

    payload: dict[str, Any] = {
        'prompt_tokens_total': prompt_total,
        'completion_tokens_total': completion_total,
        'cache_read_tokens_total': cache_read_total,
        'cache_creation_tokens_total': cache_creation_total,
        'total_tokens': prompt_total + completion_total,
        'avg_context_size': avg_context,
        'last_context_size': last_context_size,
        'turn_count': turns,
    }
    if user_id is not None:
        # Deliberately import inside the function so the business
        # module doesn't take a hard import on `iris_engine.llm.client`
        # — keeps the CLI/tests that import `case_chat` for the
        # aggregate helper working without a chatbot config in scope.
        # Read the conversation's stamped policy so the daily budget in
        # the usage bar reflects what the loop actually enforces for
        # this customer.
        from app.iris_engine.llm.client import load_config
        from app.models.chatbot_policy import ChatbotPolicy
        conv = (
            db.session.query(CaseChatConversation.resolved_policy_id)
            .filter(CaseChatConversation.id == conversation_id)
            .first()
        )
        stamped = None
        if conv and conv[0] is not None:
            stamped = ChatbotPolicy.query.filter_by(id=conv[0]).first()
        try:
            cfg = load_config(policy=stamped)
        except Exception:
            cfg = None
        payload['daily'] = {
            'user_used': sum_tokens_today(user_id=user_id),
            'org_used': sum_tokens_today(),
            'user_budget': int(cfg.daily_token_budget_per_user) if cfg else 0,
            'org_budget': int(cfg.daily_token_budget_org) if cfg else 0,
        }
    return payload


def sum_tokens_today(*, user_id: Optional[int] = None) -> int:
    """Sum prompt + completion tokens spent since midnight UTC.

    Pass `user_id` for a per-user total, omit for the org-wide sum.
    Rows without token counts (some providers only emit them on the
    final chunk; if the stream aborts we get `NULL`s) count as zero —
    prompt_bytes+response_bytes are always populated so the DPO view
    is still useful, but the budget check treats them optimistically.
    """
    midnight = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    # `db.func` is the Flask-SQLAlchemy re-export of `sqlalchemy.func`
    # — using it keeps the business layer off a direct sqlalchemy
    # import (see the "Do not import sqlalchemy from the business
    # layer" import-linter contract).
    prompt_sum = db.func.coalesce(
        db.func.sum(CaseChatEgressAudit.prompt_tokens), 0)
    completion_sum = db.func.coalesce(
        db.func.sum(CaseChatEgressAudit.completion_tokens), 0)
    query = db.session.query(prompt_sum + completion_sum).filter(
        CaseChatEgressAudit.created_at >= midnight,
    )
    if user_id is not None:
        query = query.filter(CaseChatEgressAudit.user_id == user_id)
    return int(query.scalar() or 0)


def list_egress_audit(
    *,
    limit: int = 100,
    offset: int = 0,
    user_id: Optional[int] = None,
    days: int = 30,
    conversation_id: Optional[int] = None,
) -> list[CaseChatEgressAudit]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    query = CaseChatEgressAudit.query.filter(
        CaseChatEgressAudit.created_at >= cutoff,
    )
    if user_id is not None:
        query = query.filter_by(user_id=user_id)
    if conversation_id is not None:
        query = query.filter_by(conversation_id=conversation_id)
    return (
        query
        .order_by(CaseChatEgressAudit.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


# ---- Retention + DPO -------------------------------------------------

def run_chatbot_retention() -> dict[str, int]:
    """Hard-delete conversations older than each policy's retention window.

    Iterates every ChatbotPolicy with `retention_days > 0` and deletes
    matching conversations older than that window. Conversations whose
    resolved policy has `retention_days == 0` (or NULL policy) are
    untouched — the operator opts in per policy.

    Returns a summary dict `{conversations_deleted, messages_deleted,
    egress_deleted}` for the caller to log. This is the codebase's
    **first** hard-delete retention job; the pattern (per-policy window
    + explicit opt-in) is deliberate so future retention scans don't
    silently reap unrelated data.

    Safe to call from a REST admin trigger or an external cron. Uses
    `ON DELETE CASCADE` on the child tables to fan out the delete.
    """
    from app.models.chatbot_policy import ChatbotPolicy

    now = datetime.now(timezone.utc)
    total_convs = 0
    total_msgs = 0
    total_egress = 0
    policies = (
        ChatbotPolicy.query
        .filter(ChatbotPolicy.retention_days > 0)
        .all()
    )
    for policy in policies:
        cutoff = now - timedelta(days=int(policy.retention_days))
        # Find matching conversations first so we can count children
        # before the CASCADE nukes them (SQLAlchemy 2.x reports
        # rowcount inconsistently across drivers on CASCADE).
        stale = (
            CaseChatConversation.query
            .filter(
                CaseChatConversation.resolved_policy_id == policy.id,
                CaseChatConversation.updated_at < cutoff,
            )
            .all()
        )
        if not stale:
            continue
        stale_ids = [c.id for c in stale]
        msg_count = (
            CaseChatMessage.query
            .filter(CaseChatMessage.conversation_id.in_(stale_ids))
            .count()
        )
        egress_count = (
            CaseChatEgressAudit.query
            .filter(CaseChatEgressAudit.conversation_id.in_(stale_ids))
            .count()
        )
        # Delete conversations — CASCADE handles messages, pending
        # tool calls, and egress rows.
        (
            CaseChatConversation.query
            .filter(CaseChatConversation.id.in_(stale_ids))
            .delete(synchronize_session=False)
        )
        db.session.commit()
        total_convs += len(stale_ids)
        total_msgs += msg_count
        total_egress += egress_count
    return {
        'conversations_deleted': total_convs,
        'messages_deleted': total_msgs,
        'egress_deleted': total_egress,
    }


def dpo_export_for_user(user_id: int) -> dict[str, Any]:
    """Return every conversation + message + egress row for one user.

    Data-subject-access-request (DSAR) shape — the DPO downloads this
    as JSON and hands it to the user. Includes archived conversations
    intentionally: "everything we hold" is what the subject has a
    right to.
    """
    convs = (
        CaseChatConversation.query
        .filter(CaseChatConversation.user_id == user_id)
        .order_by(CaseChatConversation.created_at.asc())
        .all()
    )
    conv_payload = []
    for conv in convs:
        msgs = list_messages(conv.id)
        egress = (
            CaseChatEgressAudit.query
            .filter(CaseChatEgressAudit.conversation_id == conv.id)
            .order_by(CaseChatEgressAudit.created_at.asc())
            .all()
        )
        conv_payload.append({
            'id': conv.id,
            'case_id': conv.case_id,
            'war_room_id': conv.war_room_id,
            'model': conv.model,
            'title': conv.title,
            'created_at': conv.created_at.isoformat() if conv.created_at else None,
            'updated_at': conv.updated_at.isoformat() if conv.updated_at else None,
            'archived_at': conv.archived_at.isoformat() if conv.archived_at else None,
            'messages': [
                {
                    'id': m.id, 'role': m.role, 'content': m.content,
                    'tool_use_id': m.tool_use_id,
                    'created_at': m.created_at.isoformat() if m.created_at else None,
                }
                for m in msgs
            ],
            'egress': [
                {
                    'id': e.id, 'provider': e.provider, 'model': e.model,
                    'request_bytes': e.request_bytes,
                    'response_bytes': e.response_bytes,
                    'prompt_tokens': e.prompt_tokens,
                    'completion_tokens': e.completion_tokens,
                    'cache_read_tokens': e.cache_read_tokens,
                    'cache_creation_tokens': e.cache_creation_tokens,
                    'redacted': e.redacted,
                    'created_at': e.created_at.isoformat() if e.created_at else None,
                }
                for e in egress
            ],
        })
    return {
        'user_id': user_id,
        'exported_at': datetime.now(timezone.utc).isoformat(),
        'conversations': conv_payload,
    }


def dpo_erase_user(user_id: int) -> dict[str, int]:
    """Hard-delete every conversation for a user.

    Right-to-erasure endpoint. Removes conversations, messages, pending
    tool calls, and egress audit rows via CASCADE. Returns counts so
    the DPO can attach a completion receipt to the request ticket.

    Note that `CaseChatEgressAudit.user_id` also carries a per-user FK
    — but that FK does not cascade delete on user removal (see the
    model). This function scopes by conversation ownership, so a user
    whose account was deleted but whose conversations still exist is
    handled correctly.
    """
    convs = (
        CaseChatConversation.query
        .filter(CaseChatConversation.user_id == user_id)
        .all()
    )
    if not convs:
        return {
            'conversations_deleted': 0,
            'messages_deleted': 0,
            'egress_deleted': 0,
        }
    conv_ids = [c.id for c in convs]
    msg_count = (
        CaseChatMessage.query
        .filter(CaseChatMessage.conversation_id.in_(conv_ids))
        .count()
    )
    egress_count = (
        CaseChatEgressAudit.query
        .filter(CaseChatEgressAudit.conversation_id.in_(conv_ids))
        .count()
    )
    (
        CaseChatConversation.query
        .filter(CaseChatConversation.id.in_(conv_ids))
        .delete(synchronize_session=False)
    )
    db.session.commit()
    return {
        'conversations_deleted': len(conv_ids),
        'messages_deleted': msg_count,
        'egress_deleted': egress_count,
    }
