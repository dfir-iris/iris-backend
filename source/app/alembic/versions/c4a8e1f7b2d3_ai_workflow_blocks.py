"""AI workflows — saved blocks.

`ai_workflow_block`: fragments of graph (nodes + edges, no trigger)
saved once and inserted into any workflow, e.g. a VirusTotal lookup.

Idempotent via `_has_table` / `index_exists`: `db.create_all()` runs
before the migrations on a fresh install.

Revision ID: c4a8e1f7b2d3
Revises: b7e2d4f9a1c6
Create Date: 2026-10-09 08:00:00.000000
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.alembic.alembic_utils import _has_table
from app.alembic.alembic_utils import index_exists


revision = 'c4a8e1f7b2d3'
down_revision = 'b7e2d4f9a1c6'
branch_labels = None
depends_on = None

_TABLE = 'ai_workflow_block'
_INDEXES = (
    ('ix_ai_workflow_block_owner', ['owner_id']),
    ('ix_ai_workflow_block_name', ['name']),
    ('ix_ai_workflow_block_created_by', ['created_by_id']),
)


def upgrade():
    if not _has_table(_TABLE):
        op.create_table(
            _TABLE,
            sa.Column('id', sa.BigInteger(), primary_key=True),
            sa.Column('uuid', postgresql.UUID(as_uuid=True), nullable=False, unique=True),
            sa.Column('name', sa.String(length=255), nullable=False),
            sa.Column('description', sa.Text(), nullable=True),
            sa.Column('category', sa.String(length=64), nullable=True),
            sa.Column('definition', postgresql.JSONB(astext_type=sa.Text()), nullable=False,
                      server_default=sa.text("'{}'::jsonb")),
            sa.Column('is_shared', sa.Boolean(), nullable=False, server_default=sa.text('false')),
            sa.Column('owner_id', sa.BigInteger(), sa.ForeignKey('user.id', ondelete='CASCADE'), nullable=True),
            sa.Column('created_by_id', sa.BigInteger(), sa.ForeignKey('user.id', ondelete='SET NULL'),
                      nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
    for name, columns in _INDEXES:
        if not index_exists(_TABLE, name):
            op.create_index(name, _TABLE, columns)


def downgrade():
    if _has_table(_TABLE):
        op.drop_table(_TABLE)
