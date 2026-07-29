"""Add war_room_id to case_chat_conversation.

Extends Yuki (the chatbot) with war-room scope. Conversations can now
be scoped to a specific war room the same way they're scoped to a
case — the tool-use loop overrides `war_room_id` on every war-room
tool call before dispatch (matches the case_id override for prompt-
injection defence).

Idempotent via `_table_has_column` — safe to re-run.

Revision ID: b8f4c2d7e91a
Revises: a1c9e57d3b8f
Create Date: 2026-07-29 15:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

from app.alembic.alembic_utils import _table_has_column


revision = 'b8f4c2d7e91a'
down_revision = 'a1c9e57d3b8f'
branch_labels = None
depends_on = None


def upgrade():
    if not _table_has_column('case_chat_conversation', 'war_room_id'):
        op.add_column(
            'case_chat_conversation',
            sa.Column(
                'war_room_id',
                sa.BigInteger(),
                sa.ForeignKey('war_room.war_room_id', ondelete='CASCADE'),
                nullable=True,
            ),
        )
        op.create_index(
            'ix_case_chat_conversation_wr_user_updated',
            'case_chat_conversation',
            ['war_room_id', 'user_id', 'updated_at'],
        )


def downgrade():
    if _table_has_column('case_chat_conversation', 'war_room_id'):
        op.drop_index(
            'ix_case_chat_conversation_wr_user_updated',
            table_name='case_chat_conversation',
        )
        op.drop_column('case_chat_conversation', 'war_room_id')
