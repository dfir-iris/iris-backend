"""Add chatbot_auto_approve_write_tools setting.

Boolean sibling of `chatbot_auto_execute_read_tools`. Off by default —
opt-in per install so on-prem operators can run Yuki full-throttle
(no Approve/Deny click on writes) once they trust it.

Idempotent via `_table_has_column`.

Revision ID: b4d21e08c7f3
Revises: a1c9d2e5f804
Create Date: 2026-08-04 10:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

from app.alembic.alembic_utils import _table_has_column


revision = 'b4d21e08c7f3'
down_revision = 'a1c9d2e5f804'
branch_labels = None
depends_on = None


def upgrade():
    if not _table_has_column('server_settings', 'chatbot_auto_approve_write_tools'):
        op.add_column(
            'server_settings',
            sa.Column(
                'chatbot_auto_approve_write_tools',
                sa.Boolean(),
                nullable=False,
                server_default=sa.text('false'),
            ),
        )


def downgrade():
    if _table_has_column('server_settings', 'chatbot_auto_approve_write_tools'):
        op.drop_column('server_settings', 'chatbot_auto_approve_write_tools')
