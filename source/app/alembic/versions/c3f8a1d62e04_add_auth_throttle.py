#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Add auth_throttle, the shared counter behind both login throttles.

The password throttle and the MFA-verify throttle each kept their
counters in a module-level dict. `app` runs under `gunicorn -w 4`, so
those were four independent counters: the documented 10-attempt password
lockout and 5-attempt TOTP lockout cost an attacker forty and twenty
attempts respectively, and a container restart returned the budget.

One row per bucket, keyed by an opaque string rather than a user id —
the MFA throttle counts per user, the password throttle also counts per
source address, and an address has no user row to reference.

Postgres rather than Redis on purpose: the login path already writes a
`UserActivity` row for every rejected attempt, so the write is paid
either way, and a counter is a poor reason to add a service to a stack
that already has six.

No back-fill: an empty table means every bucket starts fresh, which is
the same state a restart used to produce.

Idempotent via `_has_table` — safe to re-run.

Revision ID: c3f8a1d62e04
Revises: b1e7d4a09c55
Create Date: 2026-09-21
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.alembic.alembic_utils import _has_table


revision = 'c3f8a1d62e04'
down_revision = 'b1e7d4a09c55'
branch_labels = None
depends_on = None


def upgrade():
    if _has_table('auth_throttle'):
        return

    op.create_table(
        'auth_throttle',
        # Text, not a bounded String: the account bucket embeds a
        # user-supplied username, and truncating one would quietly merge
        # two accounts into a single budget.
        sa.Column('key', sa.Text(), primary_key=True),
        sa.Column('failures', sa.Integer(), nullable=False,
                  server_default=sa.text('0')),
        sa.Column('window_start', sa.DateTime(), nullable=False,
                  server_default=sa.text('now()')),
        sa.Column('locked_until', sa.DateTime(), nullable=True),
    )
    # No secondary index. Every access is by primary key: the read before
    # each login attempt, and the `ON CONFLICT (key)` upsert that counts
    # a failure.


def downgrade():
    if not _has_table('auth_throttle'):
        return

    op.drop_table('auth_throttle')
