#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Add request_snapshot to case_chat_egress_audit.

Stores the system prompt, tools list, and triggering user message sent
to the model on each turn — history stripped. Lets admins verify which
tools the model actually received without replaying the conversation.

Idempotent via `_table_has_column`.

Revision ID: b2e7f4a9c031
Revises: d1e9a3b7c528
Create Date: 2026-08-10
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.alembic.alembic_utils import _table_has_column

revision = 'b2e7f4a9c031'
down_revision = 'd1e9a3b7c528'
branch_labels = None
depends_on = None


def upgrade():
    if _table_has_column('case_chat_egress_audit', 'request_snapshot'):
        return
    op.add_column(
        'case_chat_egress_audit',
        sa.Column('request_snapshot', postgresql.JSONB(), nullable=True),
    )


def downgrade():
    if _table_has_column('case_chat_egress_audit', 'request_snapshot'):
        op.drop_column('case_chat_egress_audit', 'request_snapshot')
