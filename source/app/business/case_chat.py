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
) -> CaseChatConversation:
    conv = CaseChatConversation(
        user_id=user.id,
        case_id=case_id,
        model=model,
        title=(title or '')[:200],
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
    """Global-scope conversations (`case_id IS NULL`)."""
    query = CaseChatConversation.query.filter(
        CaseChatConversation.user_id == user.id,
        CaseChatConversation.case_id.is_(None),
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
) -> CaseChatEgressAudit:
    row = CaseChatEgressAudit(
        conversation_id=conversation.id,
        user_id=user.id,
        provider=provider,
        model=model,
        request_bytes=int(request_bytes),
        response_bytes=0,
        redacted=redacted,
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
) -> CaseChatEgressAudit:
    audit_row.response_bytes = int(response_bytes)
    if prompt_tokens is not None:
        audit_row.prompt_tokens = int(prompt_tokens)
    if completion_tokens is not None:
        audit_row.completion_tokens = int(completion_tokens)
    db.session.commit()
    return audit_row


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
) -> list[CaseChatEgressAudit]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    query = CaseChatEgressAudit.query.filter(
        CaseChatEgressAudit.created_at >= cutoff,
    )
    if user_id is not None:
        query = query.filter_by(user_id=user_id)
    return (
        query
        .order_by(CaseChatEgressAudit.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
