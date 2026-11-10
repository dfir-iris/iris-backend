#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Notifications: a severity.

`notification.severity` is `info`, `success`, `warning` or `critical`
and picks the icon of the notification in the bell. NULL (every row
written before, and the core events): no icon of its own.

Idempotent: `db.create_all()` runs before the migrations on a fresh
install and already creates the column.

Revision ID: d5f2b8c4a9e1
Revises: a6d3f8b2c917
Create Date: 2026-11-10
"""
import sqlalchemy as sa
from alembic import op

from app.alembic.alembic_utils import _table_has_column


revision = 'd5f2b8c4a9e1'
down_revision = 'a6d3f8b2c917'
branch_labels = None
depends_on = None


def upgrade():
    if not _table_has_column('notification', 'severity'):
        op.add_column('notification', sa.Column('severity', sa.String(length=16), nullable=True))


def downgrade():
    if _table_has_column('notification', 'severity'):
        op.drop_column('notification', 'severity')
