"""Add case-chat tables + chatbot_* ServerSettings columns.

Creates four tables backing the floating chatbot:
  * `case_chat_conversation` — one thread per (case_id, user_id).
  * `case_chat_message` — messages within a conversation, content as
    JSONB Anthropic-shaped content blocks for lossless round-trip.
  * `case_chat_pending_tool_call` — one row per write tool_use awaiting
    analyst approval. Makes the loop stateless across events.
  * `case_chat_egress_audit` — per-LLM-call byte + token counts for the
    admin egress-audit page.

Also adds 13 `chatbot_*` columns to `server_settings` (enable toggle,
provider config, budget caps, redaction toggles) — mirrors the shape of
the `error_reporting_*` and `mcp_*` column groups. `chatbot_api_key` is
Fernet-encrypted at rest.

Idempotent via `_has_table` / `_table_has_column` — safe to re-run.

Revision ID: e4a1c73b8f52
Revises: d2e6f9a3b420
Create Date: 2026-07-28 14:00:00.000000
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.alembic.alembic_utils import _has_table, _table_has_column


revision = 'e4a1c73b8f52'
down_revision = 'd2e6f9a3b420'
branch_labels = None
depends_on = None


_CHATBOT_COLUMNS = (
    # (name, type, server_default, nullable)
    ('chatbot_enabled', sa.Boolean(), sa.text('false'), False),
    ('chatbot_provider', sa.String(length=32), sa.text("''"), False),
    ('chatbot_api_key', sa.Text(), None, True),
    ('chatbot_model', sa.String(length=120), sa.text("''"), False),
    ('chatbot_base_url', sa.String(length=500), sa.text("''"), False),
    ('chatbot_max_turns_per_conversation', sa.Integer(),
     sa.text('25'), False),
    ('chatbot_max_tool_calls_per_turn', sa.Integer(),
     sa.text('8'), False),
    ('chatbot_auto_execute_read_tools', sa.Boolean(),
     sa.text('true'), False),
    ('chatbot_daily_token_budget_per_user', sa.Integer(),
     sa.text('500000'), False),
    ('chatbot_daily_token_budget_org', sa.Integer(),
     sa.text('10000000'), False),
    ('chatbot_redact_ips', sa.Boolean(), sa.text('false'), False),
    ('chatbot_redact_emails', sa.Boolean(), sa.text('false'), False),
    ('chatbot_redact_hashes', sa.Boolean(), sa.text('false'), False),
)


def upgrade():
    # ---- ServerSettings columns ------------------------------------
    for name, col_type, default, nullable in _CHATBOT_COLUMNS:
        if _table_has_column('server_settings', name):
            continue
        kwargs = {'nullable': nullable}
        if default is not None:
            kwargs['server_default'] = default
        op.add_column(
            'server_settings',
            sa.Column(name, col_type, **kwargs),
        )

    # ---- case_chat_conversation ------------------------------------
    if not _has_table('case_chat_conversation'):
        op.create_table(
            'case_chat_conversation',
            sa.Column('id', sa.BigInteger(), primary_key=True),
            sa.Column(
                'case_id', sa.BigInteger(),
                sa.ForeignKey('cases.case_id', ondelete='CASCADE'),
                nullable=True,
            ),
            sa.Column(
                'user_id', sa.BigInteger(),
                sa.ForeignKey('user.id', ondelete='CASCADE'),
                nullable=False,
            ),
            sa.Column('model', sa.String(length=120), nullable=False,
                      server_default=sa.text("''")),
            sa.Column('title', sa.String(length=200), nullable=False,
                      server_default=sa.text("''")),
            sa.Column('created_at', sa.DateTime(), nullable=False,
                      server_default=sa.text('now()')),
            sa.Column('updated_at', sa.DateTime(), nullable=False,
                      server_default=sa.text('now()')),
            sa.Column('archived_at', sa.DateTime(), nullable=True),
        )
        op.create_index(
            'ix_case_chat_conversation_case_user_updated',
            'case_chat_conversation',
            ['case_id', 'user_id', 'updated_at'],
        )
        op.create_index(
            'ix_case_chat_conversation_user_updated',
            'case_chat_conversation',
            ['user_id', 'updated_at'],
        )

    # ---- case_chat_message -----------------------------------------
    if not _has_table('case_chat_message'):
        op.create_table(
            'case_chat_message',
            sa.Column('id', sa.BigInteger(), primary_key=True),
            sa.Column(
                'conversation_id', sa.BigInteger(),
                sa.ForeignKey('case_chat_conversation.id',
                              ondelete='CASCADE'),
                nullable=False,
            ),
            sa.Column('role', sa.String(length=16), nullable=False),
            sa.Column('content', postgresql.JSONB(), nullable=False),
            sa.Column('tool_use_id', sa.String(length=64), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False,
                      server_default=sa.text('now()')),
        )
        op.create_index(
            'ix_case_chat_message_conversation_created',
            'case_chat_message',
            ['conversation_id', 'created_at'],
        )

    # ---- case_chat_pending_tool_call -------------------------------
    if not _has_table('case_chat_pending_tool_call'):
        op.create_table(
            'case_chat_pending_tool_call',
            sa.Column('id', sa.BigInteger(), primary_key=True),
            sa.Column(
                'conversation_id', sa.BigInteger(),
                sa.ForeignKey('case_chat_conversation.id',
                              ondelete='CASCADE'),
                nullable=False,
            ),
            sa.Column(
                'assistant_message_id', sa.BigInteger(),
                sa.ForeignKey('case_chat_message.id', ondelete='CASCADE'),
                nullable=False,
            ),
            sa.Column('tool_use_id', sa.String(length=64), nullable=False),
            sa.Column('tool_name', sa.String(length=120), nullable=False),
            sa.Column('arguments', postgresql.JSONB(), nullable=False),
            sa.Column('status', sa.String(length=16), nullable=False,
                      server_default=sa.text("'pending'")),
            sa.Column('resolved_at', sa.DateTime(), nullable=True),
            sa.Column(
                'resolved_by_user_id', sa.BigInteger(),
                sa.ForeignKey('user.id'), nullable=True,
            ),
            sa.Column('created_at', sa.DateTime(), nullable=False,
                      server_default=sa.text('now()')),
            sa.UniqueConstraint(
                'assistant_message_id', 'tool_use_id',
                name='uq_case_chat_pending_tool_call_message_use',
            ),
        )
        op.create_index(
            'ix_case_chat_pending_tool_call_conversation_status',
            'case_chat_pending_tool_call',
            ['conversation_id', 'status'],
        )

    # ---- case_chat_egress_audit ------------------------------------
    if not _has_table('case_chat_egress_audit'):
        op.create_table(
            'case_chat_egress_audit',
            sa.Column('id', sa.BigInteger(), primary_key=True),
            sa.Column(
                'conversation_id', sa.BigInteger(),
                sa.ForeignKey('case_chat_conversation.id',
                              ondelete='CASCADE'),
                nullable=False,
            ),
            sa.Column(
                'user_id', sa.BigInteger(),
                sa.ForeignKey('user.id'), nullable=False,
            ),
            sa.Column('provider', sa.String(length=32), nullable=False),
            sa.Column('model', sa.String(length=120), nullable=False),
            sa.Column('request_bytes', sa.Integer(), nullable=False,
                      server_default=sa.text('0')),
            sa.Column('response_bytes', sa.Integer(), nullable=False,
                      server_default=sa.text('0')),
            sa.Column('prompt_tokens', sa.Integer(), nullable=True),
            sa.Column('completion_tokens', sa.Integer(), nullable=True),
            sa.Column('redacted', sa.Boolean(), nullable=False,
                      server_default=sa.text('false')),
            sa.Column('created_at', sa.DateTime(), nullable=False,
                      server_default=sa.text('now()')),
        )
        op.create_index(
            'ix_case_chat_egress_audit_user_created',
            'case_chat_egress_audit',
            ['user_id', 'created_at'],
        )
        op.create_index(
            'ix_case_chat_egress_audit_created',
            'case_chat_egress_audit',
            ['created_at'],
        )


def downgrade():
    if _has_table('case_chat_egress_audit'):
        op.drop_index('ix_case_chat_egress_audit_created',
                      table_name='case_chat_egress_audit')
        op.drop_index('ix_case_chat_egress_audit_user_created',
                      table_name='case_chat_egress_audit')
        op.drop_table('case_chat_egress_audit')

    if _has_table('case_chat_pending_tool_call'):
        op.drop_index('ix_case_chat_pending_tool_call_conversation_status',
                      table_name='case_chat_pending_tool_call')
        op.drop_table('case_chat_pending_tool_call')

    if _has_table('case_chat_message'):
        op.drop_index('ix_case_chat_message_conversation_created',
                      table_name='case_chat_message')
        op.drop_table('case_chat_message')

    if _has_table('case_chat_conversation'):
        op.drop_index('ix_case_chat_conversation_user_updated',
                      table_name='case_chat_conversation')
        op.drop_index('ix_case_chat_conversation_case_user_updated',
                      table_name='case_chat_conversation')
        op.drop_table('case_chat_conversation')

    for name, _t, _d, _n in reversed(_CHATBOT_COLUMNS):
        if _table_has_column('server_settings', name):
            op.drop_column('server_settings', name)
