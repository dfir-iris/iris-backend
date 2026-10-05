"""Native webhooks — add webhook.manual_label.

The entry a webhook subscribed to `on_manual_trigger_*` hooks shows in
the object menus. Empty falls back to the webhook name.

Idempotent via `_table_has_column`: `db.create_all()` runs before the
migrations on a fresh install and already creates the column.

Revision ID: c4e8f1a2d905
Revises: b7d3e9a4c612
Create Date: 2026-10-05 14:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

from app.alembic.alembic_utils import _table_has_column


revision = 'c4e8f1a2d905'
down_revision = 'b7d3e9a4c612'
branch_labels = None
depends_on = None


def upgrade():
    if not _table_has_column('webhook', 'manual_label'):
        op.add_column('webhook', sa.Column('manual_label', sa.String(length=255), nullable=True))


def downgrade():
    if _table_has_column('webhook', 'manual_label'):
        op.drop_column('webhook', 'manual_label')
