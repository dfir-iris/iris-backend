"""Add per-customer chatbot policy tables and stamps.

Creates:
  * `chatbot_policy` — named provider/model/redaction/budget/retention
    profiles selectable per customer.
  * `client.chatbot_policy_id` — nullable FK. NULL = global default.
  * `case_chat_conversation.resolved_policy_id` +
    `resolved_restriction_level` — the policy snapshot the resolver
    stamped at creation, re-checked on every send() and on war-room
    case attach/detach.

Idempotent via `_has_table` / `_table_has_column`.

Revision ID: d1e9a3b7c528
Revises: c5a2f8e4d716
Create Date: 2026-08-07 10:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

from app.alembic.alembic_utils import _has_table, _table_has_column


revision = 'd1e9a3b7c528'
down_revision = 'c5a2f8e4d716'
branch_labels = None
depends_on = None


def upgrade():
    # ---- chatbot_policy table --------------------------------------
    if not _has_table('chatbot_policy'):
        op.create_table(
            'chatbot_policy',
            sa.Column('id', sa.BigInteger(), primary_key=True),
            sa.Column('name', sa.String(length=120), nullable=False, unique=True),
            sa.Column('description', sa.Text(), nullable=False,
                      server_default=sa.text("''")),
            sa.Column('restriction_level', sa.Integer(), nullable=False,
                      server_default=sa.text('10')),
            sa.Column('provider', sa.String(length=32), nullable=False,
                      server_default=sa.text("''")),
            sa.Column('model', sa.String(length=120), nullable=False,
                      server_default=sa.text("''")),
            sa.Column('api_key', sa.Text(), nullable=True),
            sa.Column('base_url', sa.String(length=500), nullable=False,
                      server_default=sa.text("''")),
            sa.Column('auto_execute_read_tools', sa.Boolean(), nullable=False,
                      server_default=sa.text('true')),
            sa.Column('auto_approve_write_tools', sa.Boolean(), nullable=False,
                      server_default=sa.text('false')),
            sa.Column('max_turns_per_conversation', sa.Integer(),
                      nullable=False, server_default=sa.text('25')),
            sa.Column('max_tool_calls_per_turn', sa.Integer(),
                      nullable=False, server_default=sa.text('8')),
            sa.Column('daily_token_budget_per_user', sa.Integer(),
                      nullable=False, server_default=sa.text('500000')),
            sa.Column('daily_token_budget_org', sa.Integer(),
                      nullable=False, server_default=sa.text('10000000')),
            sa.Column('redact_ips', sa.Boolean(), nullable=False,
                      server_default=sa.text('false')),
            sa.Column('redact_emails', sa.Boolean(), nullable=False,
                      server_default=sa.text('false')),
            sa.Column('redact_hashes', sa.Boolean(), nullable=False,
                      server_default=sa.text('false')),
            sa.Column('retention_days', sa.Integer(), nullable=False,
                      server_default=sa.text('0')),
            sa.Column('created_at', sa.DateTime(), nullable=False,
                      server_default=sa.text('now()')),
            sa.Column('updated_at', sa.DateTime(), nullable=False,
                      server_default=sa.text('now()')),
        )

    # ---- client.chatbot_policy_id ----------------------------------
    if not _table_has_column('client', 'chatbot_policy_id'):
        op.add_column(
            'client',
            sa.Column(
                'chatbot_policy_id', sa.BigInteger(),
                sa.ForeignKey('chatbot_policy.id', ondelete='SET NULL'),
                nullable=True,
            ),
        )

    # ---- case_chat_conversation stamps -----------------------------
    if not _table_has_column('case_chat_conversation', 'resolved_policy_id'):
        op.add_column(
            'case_chat_conversation',
            sa.Column(
                'resolved_policy_id', sa.BigInteger(),
                sa.ForeignKey('chatbot_policy.id', ondelete='SET NULL'),
                nullable=True,
            ),
        )
    if not _table_has_column('case_chat_conversation',
                             'resolved_restriction_level'):
        op.add_column(
            'case_chat_conversation',
            sa.Column(
                'resolved_restriction_level', sa.Integer(),
                nullable=False, server_default=sa.text('0'),
            ),
        )


def downgrade():
    if _table_has_column('case_chat_conversation',
                         'resolved_restriction_level'):
        op.drop_column('case_chat_conversation',
                       'resolved_restriction_level')
    if _table_has_column('case_chat_conversation', 'resolved_policy_id'):
        op.drop_column('case_chat_conversation', 'resolved_policy_id')
    if _table_has_column('client', 'chatbot_policy_id'):
        op.drop_column('client', 'chatbot_policy_id')
    if _has_table('chatbot_policy'):
        op.drop_table('chatbot_policy')
