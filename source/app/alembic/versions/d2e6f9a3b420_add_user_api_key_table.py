"""Add user_api_key table for named, revocable, scope-restricted API keys.

Creates `user_api_key` and back-fills a `name='legacy'` row per existing
user, hashing the user's `User.api_key` column into `key_hash`. That
guarantees every existing key keeps working after the migration — the
lookup path checks the new table first, so calls that used to hit
`_get_user_by_api_key(User.api_key)` now find the same user via the
hashed row and inherit their full permissions (`scope_mask=NULL`).

`User.api_key` stays in place for one release as a compatibility mirror.
The follow-up migration will drop it once we've confirmed no external
automation still reads it out via `POST /me/api-key/renew`.

Idempotent via `_has_table` / `_table_has_column` — safe to re-run.

Revision ID: d2e6f9a3b420
Revises: c1d5e8f2a319
Create Date: 2026-07-28 12:00:00.000000
"""
import hashlib

import sqlalchemy as sa
from alembic import op

from app.alembic.alembic_utils import _has_table


revision = 'd2e6f9a3b420'
down_revision = 'c1d5e8f2a319'
branch_labels = None
depends_on = None


def upgrade():
    if not _has_table('user_api_key'):
        op.create_table(
            'user_api_key',
            sa.Column('id', sa.BigInteger(), primary_key=True),
            sa.Column(
                'user_id', sa.BigInteger(),
                sa.ForeignKey('user.id', ondelete='CASCADE'),
                nullable=False,
            ),
            sa.Column('name', sa.String(length=120), nullable=False),
            sa.Column('key_hash', sa.String(length=64), nullable=False),
            sa.Column('scope_mask', sa.BigInteger(), nullable=True),
            sa.Column(
                'created_at', sa.DateTime(), nullable=False,
                server_default=sa.text('now()'),
            ),
            sa.Column('last_used_at', sa.DateTime(), nullable=True),
            sa.Column('revoked_at', sa.DateTime(), nullable=True),
            sa.UniqueConstraint('user_id', 'name',
                                name='uq_user_api_key_user_name'),
            sa.UniqueConstraint('key_hash', name='uq_user_api_key_key_hash'),
        )
        op.create_index(
            'ix_user_api_key_user_id',
            'user_api_key',
            ['user_id'],
        )

    # Back-fill: one row per user whose api_key is set. Skip users
    # already mirrored (idempotent re-runs / partial upgrades) by
    # checking key_hash uniqueness. The seeded administrator is the
    # obvious case; every real user should get a row too so their
    # existing key keeps authenticating via the new path.
    bind = op.get_bind()
    users = bind.execute(sa.text(
        "SELECT id, api_key FROM \"user\" "
        "WHERE api_key IS NOT NULL AND api_key <> ''"
    )).fetchall()
    for row in users:
        user_id, api_key = row[0], row[1]
        key_hash = hashlib.sha256(api_key.encode('utf-8')).hexdigest()
        existing = bind.execute(
            sa.text('SELECT 1 FROM user_api_key WHERE key_hash = :h'),
            {'h': key_hash},
        ).first()
        if existing:
            continue
        bind.execute(sa.text(
            "INSERT INTO user_api_key (user_id, name, key_hash, scope_mask) "
            "VALUES (:user_id, 'legacy', :key_hash, NULL)"
        ), {'user_id': user_id, 'key_hash': key_hash})


def downgrade():
    if _has_table('user_api_key'):
        op.drop_index('ix_user_api_key_user_id', table_name='user_api_key')
        op.drop_table('user_api_key')
