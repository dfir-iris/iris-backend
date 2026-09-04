#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Add user_auth_session for JWT rotation and revocation.

VI-004: access and refresh tokens were self-contained HS256 blobs with
no server-side counterpart, so nothing could invalidate one. A refresh
token captured in transit stayed usable for its full fourteen days, in
parallel with the victim's own, and `/auth/logout` cleared a cookie
without touching the credentials that actually granted access.

This table is the handle the auth code needs: one row per token family,
keyed by the `sid` claim that now rides in every token, holding the
`jti` of the single refresh token the family currently accepts.

No back-fill is possible or wanted. Tokens issued before this migration
carry no `sid` and are refused outright by `validate_auth_token` and
`api_auth._jwt_user`, so deploying this logs every active user out once.
That is the intended behaviour: grandfathering the old tokens would keep
exactly the un-revocable credentials the finding is about.

Idempotent via `_has_table` — safe to re-run.

Revision ID: f4b7c1e0a92d
Revises: e3f1b8c2d094
Create Date: 2026-09-04
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.alembic.alembic_utils import _has_table


revision = 'f4b7c1e0a92d'
down_revision = 'e3f1b8c2d094'
branch_labels = None
depends_on = None


def upgrade():
    if _has_table('user_auth_session'):
        return

    op.create_table(
        'user_auth_session',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('sid', sa.String(length=36), nullable=False),
        sa.Column(
            'user_id', sa.BigInteger(),
            sa.ForeignKey('user.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('refresh_jti', sa.String(length=36), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(), nullable=False,
            server_default=sa.text('now()'),
        ),
        sa.Column('last_used_at', sa.DateTime(), nullable=True),
        sa.Column('revoked_at', sa.DateTime(), nullable=True),
    )
    # A *unique index* rather than a unique constraint plus a plain index:
    # `UserAuthSession.sid` is declared `unique=True, index=True`, which is
    # the single object SQLAlchemy emits for that pair. Fresh installs get
    # their schema from `db.create_all()` and upgrades get it from here, so
    # the two have to agree down to the name or the paths diverge.
    # It also happens to be what we want: the per-request access-token gate
    # probes this index on every authenticated call.
    op.create_index('ix_user_auth_session_sid', 'user_auth_session', ['sid'],
                    unique=True)
    # Backs the per-user lookups (revoke-all-sessions for a given account).
    op.create_index('ix_user_auth_session_user_id', 'user_auth_session', ['user_id'])


def downgrade():
    if not _has_table('user_auth_session'):
        return

    op.drop_index('ix_user_auth_session_user_id', table_name='user_auth_session')
    op.drop_index('ix_user_auth_session_sid', table_name='user_auth_session')
    op.drop_table('user_auth_session')
