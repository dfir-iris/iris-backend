"""Add banner table for admin-managed top banners.

A `banner` row is a scheduled, in-app announcement created by a server
administrator (Settings → Banners). Every authenticated user sees the row
rendered as a colored strip at the top of the app while its timespan is
current — `start_at IS NULL OR start_at <= now()` AND
`end_at IS NULL OR end_at > now()`.

Idempotent via `_has_table`.

Revision ID: a1c9d2e5f804
Revises: e2b47f01c5d3
Create Date: 2026-08-03 12:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

from app.alembic.alembic_utils import _has_table


revision = 'a1c9d2e5f804'
down_revision = 'e2b47f01c5d3'
branch_labels = None
depends_on = None


def upgrade():
    if not _has_table('banner'):
        op.create_table(
            'banner',
            sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column('text', sa.Text(), nullable=False),
            sa.Column('purpose', sa.String(length=16), nullable=False),
            sa.Column('dismissable', sa.Boolean(), nullable=False,
                      server_default=sa.text('true')),
            sa.Column('start_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('end_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False,
                      server_default=sa.text('now()')),
            sa.Column('updated_at', sa.DateTime(), nullable=False,
                      server_default=sa.text('now()')),
            sa.Column('created_by', sa.BigInteger(), nullable=True),
            sa.ForeignKeyConstraint(['created_by'], ['user.id'],
                                    ondelete='SET NULL'),
            sa.PrimaryKeyConstraint('id'),
            # DB-level guard on the purpose values. The marshmallow schema
            # is the primary validator, but the CHECK keeps the invariant
            # honest against direct SQL / shell inserts too.
            sa.CheckConstraint(
                "purpose IN ('info','warning','error')",
                name='ck_banner_purpose_valid',
            ),
            # start < end when both are provided. NULLs make the
            # predicate NULL — Postgres treats that as "constraint not
            # violated" — so an unbounded side is allowed.
            sa.CheckConstraint(
                'end_at IS NULL OR start_at IS NULL OR end_at > start_at',
                name='ck_banner_timespan_valid',
            ),
        )


def downgrade():
    if _has_table('banner'):
        op.drop_table('banner')
