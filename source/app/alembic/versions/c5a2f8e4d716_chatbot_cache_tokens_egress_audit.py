"""Add cache_read_tokens + cache_creation_tokens to case_chat_egress_audit.

Anthropic prompt caching returns two counters on `message_start.usage`
that we weren't tracking before: `cache_read_input_tokens` (hits) and
`cache_creation_input_tokens` (writes). OpenAI has `cached_tokens` on
`prompt_tokens_details` which maps to cache_read; Ollama has no
equivalent. Both new columns are nullable — pre-existing rows and rows
from providers without cache reporting stay NULL and the aggregate
helper coalesces to 0.

Idempotent via `_table_has_column`.

Revision ID: c5a2f8e4d716
Revises: b4d21e08c7f3
Create Date: 2026-08-07 09:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

from app.alembic.alembic_utils import _table_has_column


revision = 'c5a2f8e4d716'
down_revision = 'b4d21e08c7f3'
branch_labels = None
depends_on = None


_NEW_COLUMNS = (
    'cache_read_tokens',
    'cache_creation_tokens',
)


def upgrade():
    for name in _NEW_COLUMNS:
        if _table_has_column('case_chat_egress_audit', name):
            continue
        op.add_column(
            'case_chat_egress_audit',
            sa.Column(name, sa.Integer(), nullable=True),
        )


def downgrade():
    for name in _NEW_COLUMNS:
        if _table_has_column('case_chat_egress_audit', name):
            op.drop_column('case_chat_egress_audit', name)
