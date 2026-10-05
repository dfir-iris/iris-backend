"""Native webhooks — add webhook + webhook_delivery tables.

`webhook` holds one outbound integration: subscribed events, optional
condition, and the full request shape (method, URL, query, headers,
auth, body template, TLS / timeout / retry / redirect behaviour).
Secret values are stored encrypted with the app SECRET_KEY.

`webhook_delivery` is the delivery log, one row per event and webhook,
pruned after `IRIS_WEBHOOKS_DELIVERY_RETENTION_DAYS`.

Idempotent via `_has_table` / `index_exists`: `db.create_all()` runs
before the migrations on a fresh install and creates both tables.

Revision ID: b7d3e9a4c612
Revises: e5a2c8f17b93
Create Date: 2026-10-05 10:00:00.000000
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.alembic.alembic_utils import _has_table
from app.alembic.alembic_utils import index_exists


revision = 'b7d3e9a4c612'
down_revision = 'e5a2c8f17b93'
branch_labels = None
depends_on = None


def upgrade():
    if not _has_table('webhook'):
        op.create_table(
            'webhook',
            sa.Column('id', sa.BigInteger(), primary_key=True),
            sa.Column('name', sa.String(length=255), nullable=False),
            sa.Column('description', sa.Text(), nullable=True),
            sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.text('true')),
            sa.Column('events', sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
            sa.Column('condition', sa.Text(), nullable=True),
            sa.Column('method', sa.String(length=10), nullable=False, server_default=sa.text("'POST'")),
            sa.Column('url', sa.Text(), nullable=False),
            sa.Column('query_params', sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
            sa.Column('headers', sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
            sa.Column('auth_type', sa.String(length=16), nullable=False, server_default=sa.text("'none'")),
            sa.Column('auth_username', sa.String(length=255), nullable=True),
            sa.Column('auth_secret', sa.Text(), nullable=True),
            sa.Column('body_mode', sa.String(length=16), nullable=False, server_default=sa.text("'default'")),
            sa.Column('body_template', sa.Text(), nullable=True),
            sa.Column('content_type', sa.String(length=255), nullable=False,
                      server_default=sa.text("'application/json'")),
            sa.Column('signing_secret', sa.Text(), nullable=True),
            sa.Column('verify_tls', sa.Boolean(), nullable=False, server_default=sa.text('true')),
            sa.Column('timeout_seconds', sa.Integer(), nullable=False, server_default=sa.text('10')),
            sa.Column('max_retries', sa.Integer(), nullable=False, server_default=sa.text('3')),
            sa.Column('follow_redirects', sa.Boolean(), nullable=False, server_default=sa.text('false')),
            sa.Column('created_by_id', sa.BigInteger(),
                      sa.ForeignKey('user.id', ondelete='SET NULL'), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )

    if not _has_table('webhook_delivery'):
        op.create_table(
            'webhook_delivery',
            sa.Column('id', sa.BigInteger(), primary_key=True),
            sa.Column('uuid', postgresql.UUID(as_uuid=True), nullable=False, unique=True),
            sa.Column('webhook_id', sa.BigInteger(),
                      sa.ForeignKey('webhook.id', ondelete='CASCADE'), nullable=False),
            sa.Column('event', sa.String(length=128), nullable=False),
            sa.Column('trigger', sa.String(length=16), nullable=False, server_default=sa.text("'event'")),
            sa.Column('status', sa.String(length=16), nullable=False, server_default=sa.text("'pending'")),
            sa.Column('attempts', sa.Integer(), nullable=False, server_default=sa.text('0')),
            sa.Column('payload', sa.JSON(), nullable=True),
            sa.Column('request_method', sa.String(length=10), nullable=True),
            sa.Column('request_url', sa.Text(), nullable=True),
            sa.Column('request_headers', sa.JSON(), nullable=True),
            sa.Column('request_body', sa.Text(), nullable=True),
            sa.Column('response_status', sa.Integer(), nullable=True),
            sa.Column('response_headers', sa.JSON(), nullable=True),
            sa.Column('response_body', sa.Text(), nullable=True),
            sa.Column('error', sa.Text(), nullable=True),
            sa.Column('duration_ms', sa.Integer(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column('completed_at', sa.DateTime(), nullable=True),
        )

    if not index_exists('webhook_delivery', 'ix_webhook_delivery_webhook_created'):
        op.create_index('ix_webhook_delivery_webhook_created', 'webhook_delivery',
                        ['webhook_id', 'created_at'])
    if not index_exists('webhook_delivery', 'ix_webhook_delivery_created_at'):
        op.create_index('ix_webhook_delivery_created_at', 'webhook_delivery', ['created_at'])


def downgrade():
    if _has_table('webhook_delivery'):
        op.drop_table('webhook_delivery')
    if _has_table('webhook'):
        op.drop_table('webhook')
