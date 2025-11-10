"""Add tables for custom dashboards

Revision ID: e5d79b8c4a55
Revises: d6c49c5435c2
Create Date: 2025-11-10 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.alembic.alembic_utils import _has_table

# revision identifiers, used by Alembic.
revision = 'e5d79b8c4a55'
down_revision = 'd5a720d1b99b'
branch_labels = None
depends_on = None


def upgrade():
    if not _has_table('custom_dashboard'):
        op.create_table(
            'custom_dashboard',
            sa.Column('id', sa.Integer, primary_key=True),
            sa.Column('dashboard_uuid', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False, unique=True),
            sa.Column('name', sa.String(length=255), nullable=False),
            sa.Column('description', sa.Text, nullable=True),
            sa.Column('owner_id', sa.Integer, sa.ForeignKey('user.id'), nullable=False),
            sa.Column('is_shared', sa.Boolean, nullable=False, server_default=sa.text('false')),
            sa.Column('definition', postgresql.JSONB, nullable=True),
            sa.Column('created_at', sa.DateTime, server_default=sa.func.now(), nullable=False),
            sa.Column('updated_at', sa.DateTime, server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False)
        )

    if not _has_table('custom_dashboard_widget'):
        op.create_table(
            'custom_dashboard_widget',
            sa.Column('id', sa.Integer, primary_key=True),
            sa.Column('widget_uuid', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False, unique=True),
            sa.Column('dashboard_id', sa.Integer, sa.ForeignKey('custom_dashboard.id', ondelete='CASCADE'), nullable=False),
            sa.Column('name', sa.String(length=255), nullable=False),
            sa.Column('chart_type', sa.String(length=64), nullable=False),
            sa.Column('definition', postgresql.JSONB, nullable=False),
            sa.Column('position', sa.Integer, nullable=False, server_default=sa.text('0')),
            sa.Column('created_at', sa.DateTime, server_default=sa.func.now(), nullable=False),
            sa.Column('updated_at', sa.DateTime, server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False)
        )
        op.create_index('ix_custom_dashboard_widget_dashboard_id', 'custom_dashboard_widget', ['dashboard_id'])



def downgrade():
    if _has_table('custom_dashboard_widget'):
        op.drop_index('ix_custom_dashboard_widget_dashboard_id', table_name='custom_dashboard_widget')
        op.drop_table('custom_dashboard_widget')

    if _has_table('custom_dashboard'):
        op.drop_table('custom_dashboard')
