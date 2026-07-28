#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 3 of the License, or (at your option) any later version.

"""Case-chat models: conversation + message + pending-tool-call + egress-audit.

The chatbot's tool-use loop is *stateless across events*: every LLM turn
boundary is persisted as messages here, and every write tool_use pauses
into a `case_chat_pending_tool_call` row. That lets `send`, `approve_tool`,
and `deny_tool` each run one loop iteration by re-reading history from
Postgres — no in-memory pause primitives, tab-close is a no-op, horizontal
scale-out works.

`case_chat_egress_audit` closes the "what PII did we send to a third-party
LLM on 2026-07-27" gap: one row per `provider.stream_completion` call
with byte + token counts, surfaced through the admin egress-audit page.
"""

from sqlalchemy import BigInteger
from sqlalchemy import Boolean
from sqlalchemy import Column
from sqlalchemy import DateTime
from sqlalchemy import ForeignKey
from sqlalchemy import Index
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import UniqueConstraint
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.db import db


# Role values on `case_chat_message.role`. Kept as string constants
# rather than an enum so JSONB round-trips through the SPA are cheap and
# adding a new role (e.g. 'system' for per-conversation prompt override)
# doesn't require a migration.
ROLE_USER = 'user'
ROLE_ASSISTANT = 'assistant'
ROLE_TOOL = 'tool'

# Pending-tool-call lifecycle. `expired` is set by a periodic janitor
# (out-of-scope for v1) so old paused writes don't linger forever.
STATUS_PENDING = 'pending'
STATUS_APPROVED = 'approved'
STATUS_DENIED = 'denied'
STATUS_EXPIRED = 'expired'


class CaseChatConversation(db.Model):
    """One chat thread scoped to `(case_id, user_id)`.

    `case_id` is nullable because the SPA's floating panel is global —
    an analyst on the dashboard can start a conversation with no case
    scope. Case-scoped conversations auto-inject `case_identifier` on
    every tool call via a hard override (see loop.py) so the LLM cannot
    redirect the analyst to a different case even if prompt-injected.
    """
    __tablename__ = 'case_chat_conversation'

    id = Column(BigInteger, primary_key=True)
    case_id = Column(
        BigInteger,
        ForeignKey('cases.case_id', ondelete='CASCADE'),
        nullable=True,
    )
    user_id = Column(
        BigInteger,
        ForeignKey('user.id', ondelete='CASCADE'),
        nullable=False,
    )
    model = Column(String(120), nullable=False, default='',
                   server_default=text("''"))
    # Title is populated from the first 80 chars of the user's first
    # turn — we deliberately do NOT spend an extra LLM call on
    # summarisation. Analysts can rename later.
    title = Column(String(200), nullable=False, default='',
                   server_default=text("''"))
    created_at = Column(DateTime, nullable=False,
                        server_default=text('now()'))
    updated_at = Column(DateTime, nullable=False,
                        server_default=text('now()'))
    archived_at = Column(DateTime, nullable=True)

    user = relationship('User')
    # No ORM `case` relationship — same reason `UserActivity.case`
    # was left off: `Cases` may not be mapper-ready at import time in
    # every code path. Consumers join via Core queries when they need it.

    messages = relationship(
        'CaseChatMessage', back_populates='conversation',
        cascade='all, delete-orphan', order_by='CaseChatMessage.created_at',
    )

    __table_args__ = (
        Index('ix_case_chat_conversation_case_user_updated',
              'case_id', 'user_id', 'updated_at'),
        Index('ix_case_chat_conversation_user_updated',
              'user_id', 'updated_at'),
    )


class CaseChatMessage(db.Model):
    """One turn in a chat thread — user / assistant / tool.

    `content` is a JSONB array of Anthropic-shaped content blocks:
    `[{type: 'text', text: '...'}, {type: 'tool_use', id, name, input}, ...]`
    for assistant messages, or `[{type: 'tool_result', tool_use_id, content}]`
    for tool messages. That native shape round-trips lossless-ly through
    the persistence layer even when the wire provider is OpenAI or
    Ollama — the provider adapters normalise on ingress/egress.
    """
    __tablename__ = 'case_chat_message'

    id = Column(BigInteger, primary_key=True)
    conversation_id = Column(
        BigInteger,
        ForeignKey('case_chat_conversation.id', ondelete='CASCADE'),
        nullable=False,
    )
    role = Column(String(16), nullable=False)
    content = Column(JSONB, nullable=False)
    # Only set when role='tool' — the tool_use_id the result answers to.
    # Not a FK because pending_tool_call rows also carry this id and
    # the two tables are related-but-not-hierarchical.
    tool_use_id = Column(String(64), nullable=True)
    created_at = Column(DateTime, nullable=False,
                        server_default=text('now()'))

    conversation = relationship(
        'CaseChatConversation', back_populates='messages')

    __table_args__ = (
        Index('ix_case_chat_message_conversation_created',
              'conversation_id', 'created_at'),
    )


class CaseChatPendingToolCall(db.Model):
    """A write tool_use awaiting analyst approval.

    Created when the loop's LLM stream emits a `tool_use` block whose
    tool is in WRITE_TOOLS. Resolved by the `approve_tool` / `deny_tool`
    socket handlers via `SELECT ... FOR UPDATE` to prevent double-approve
    races. Anthropic can emit multiple `tool_use` blocks in one turn;
    each becomes a separate row, and the loop only re-runs the LLM once
    all rows for the same `assistant_message_id` are resolved.
    """
    __tablename__ = 'case_chat_pending_tool_call'

    id = Column(BigInteger, primary_key=True)
    conversation_id = Column(
        BigInteger,
        ForeignKey('case_chat_conversation.id', ondelete='CASCADE'),
        nullable=False,
    )
    assistant_message_id = Column(
        BigInteger,
        ForeignKey('case_chat_message.id', ondelete='CASCADE'),
        nullable=False,
    )
    # `tool_use_id` comes from the LLM's block — a short opaque
    # string. Together with `assistant_message_id` it uniquely
    # identifies the pending action across the turn.
    tool_use_id = Column(String(64), nullable=False)
    tool_name = Column(String(120), nullable=False)
    arguments = Column(JSONB, nullable=False)
    status = Column(String(16), nullable=False, default=STATUS_PENDING,
                    server_default=text(f"'{STATUS_PENDING}'"))
    resolved_at = Column(DateTime, nullable=True)
    resolved_by_user_id = Column(
        BigInteger, ForeignKey('user.id'), nullable=True)
    created_at = Column(DateTime, nullable=False,
                        server_default=text('now()'))

    conversation = relationship('CaseChatConversation')
    assistant_message = relationship('CaseChatMessage')
    resolved_by = relationship('User')

    __table_args__ = (
        UniqueConstraint('assistant_message_id', 'tool_use_id',
                         name='uq_case_chat_pending_tool_call_message_use'),
        Index('ix_case_chat_pending_tool_call_conversation_status',
              'conversation_id', 'status'),
    )


class CaseChatEgressAudit(db.Model):
    """One row per `provider.stream_completion` call.

    Answers "what left the box on 2026-07-27" for the DPO. Byte counts
    are always populated; token counts are filled from the provider's
    `usage` block when present (Anthropic emits it on `message_delta`
    stop; OpenAI on the final chunk; Ollama in the final JSON).
    `redacted` records whether the redactor scrubbed the request before
    it left — off by default (redaction breaks IOC pivoting) but on for
    strict-DLP installs.
    """
    __tablename__ = 'case_chat_egress_audit'

    id = Column(BigInteger, primary_key=True)
    conversation_id = Column(
        BigInteger,
        ForeignKey('case_chat_conversation.id', ondelete='CASCADE'),
        nullable=False,
    )
    user_id = Column(
        BigInteger, ForeignKey('user.id'), nullable=False)
    provider = Column(String(32), nullable=False)
    model = Column(String(120), nullable=False)
    request_bytes = Column(Integer, nullable=False, default=0,
                           server_default=text('0'))
    response_bytes = Column(Integer, nullable=False, default=0,
                            server_default=text('0'))
    prompt_tokens = Column(Integer, nullable=True)
    completion_tokens = Column(Integer, nullable=True)
    redacted = Column(Boolean, nullable=False, default=False,
                      server_default=text('false'))
    created_at = Column(DateTime, nullable=False,
                        server_default=text('now()'))

    conversation = relationship('CaseChatConversation')
    user = relationship('User')

    __table_args__ = (
        Index('ix_case_chat_egress_audit_user_created',
              'user_id', 'created_at'),
        Index('ix_case_chat_egress_audit_created',
              'created_at'),
    )
