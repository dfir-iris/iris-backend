"""Native webhooks — add webhook.use_proxy.

Off, a webhook connects directly instead of through the server settings
or environment proxy. Existing webhooks keep using the proxy.

Idempotent via `_table_has_column`: `db.create_all()` runs before the
migrations on a fresh install and already creates the column.

Revision ID: d2a6b8c1e374
Revises: c4e8f1a2d905
Create Date: 2026-10-05 16:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

from app.alembic.alembic_utils import _table_has_column


revision = 'd2a6b8c1e374'
down_revision = 'c4e8f1a2d905'
branch_labels = None
depends_on = None


def upgrade():
    if not _table_has_column('webhook', 'use_proxy'):
        op.add_column('webhook', sa.Column('use_proxy', sa.Boolean(), nullable=False, server_default=sa.text('true')))


def downgrade():
    if _table_has_column('webhook', 'use_proxy'):
        op.drop_column('webhook', 'use_proxy')
