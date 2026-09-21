#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Record OIDC provenance on user_auth_session, and close every live family.

Two changes that belong to the same upgrade boundary.

`is_oidc` marks a token family that was opened by an OIDC exchange. Those
users are exempt from IRIS's own MFA — the identity provider owns the
second factor — but nothing recorded that anywhere the refresh path could
read it, so a refresh had no way to tell an OIDC session apart from a
local session that had simply never been challenged. The claims are
identical in both cases. Server-side on the session row rather than as a
token claim: the refresh path already loads this row, and a "skip MFA"
flag is a poor thing to carry in a bearer token.

The revocation is the other half. The MFA policy used to be cached per
gunicorn worker, so a worker that had gone stale could mint a token
flagged as MFA-verified for a caller who had only ever presented a
password. Such a token is by construction indistinguishable after the
fact from one that cleared a real second factor, so there is nothing to
detect and nothing to filter — the only honest containment is to close
every family that is still open and have everyone log in once. It is also
what bounds the `is_oidc` back-fill: existing rows default to false, and
revoking them means no OIDC user is left holding a session that would be
demoted on its next refresh.

This repeats the call f4b7c1e0a92d already made for the same table and
the same reason. One forced re-login per upgrade.

Idempotent: the column is added only when absent, and re-running the
UPDATE simply matches no rows the second time.

Revision ID: b1e7d4a09c55
Revises: a8d2c5f1b743
Create Date: 2026-09-21
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.alembic.alembic_utils import _has_table
from app.alembic.alembic_utils import _table_has_column


revision = 'b1e7d4a09c55'
down_revision = 'a8d2c5f1b743'
branch_labels = None
depends_on = None


def upgrade():
    if not _has_table('user_auth_session'):
        return

    if not _table_has_column('user_auth_session', 'is_oidc'):
        op.add_column(
            'user_auth_session',
            sa.Column('is_oidc', sa.Boolean(), nullable=False,
                      server_default=sa.text('false')),
        )

    op.execute(
        'UPDATE user_auth_session SET revoked_at = now() WHERE revoked_at IS NULL'
    )


def downgrade():
    if not _table_has_column('user_auth_session', 'is_oidc'):
        return

    # The revocation is not undone: those sessions are gone either way, and
    # re-opening them would hand back the very credentials this revision
    # took away.
    op.drop_column('user_auth_session', 'is_oidc')
