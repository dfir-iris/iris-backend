"""War-room tasks assignable to war-room teams.

- `war_room_task_team`: a war-room task assigned to any number of teams
  of the same room, on top of its single `assignee_id`. Both foreign
  keys cascade: deleting the task or the team drops the assignment.

Idempotent via `_has_table` / `index_exists`: `db.create_all()` runs
before the migrations on a fresh install and already creates the table.

Revision ID: b5e8c1d4a7f2
Revises: a9d4e2f6c813
Create Date: 2026-10-06 20:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

from app.alembic.alembic_utils import _has_table
from app.alembic.alembic_utils import index_exists


revision = 'b5e8c1d4a7f2'
down_revision = 'a9d4e2f6c813'
branch_labels = None
depends_on = None


def upgrade():
    if not _has_table('war_room_task_team'):
        op.create_table(
            'war_room_task_team',
            sa.Column('task_id', sa.BigInteger(),
                      sa.ForeignKey('war_room_task.task_id', ondelete='CASCADE'), primary_key=True),
            sa.Column('team_id', sa.BigInteger(),
                      sa.ForeignKey('war_room_team.team_id', ondelete='CASCADE'), primary_key=True),
            sa.Column('assigned_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
            sa.Column('assigned_by_id', sa.BigInteger(),
                      sa.ForeignKey('user.id', ondelete='SET NULL'), nullable=True),
        )
    if not index_exists('war_room_task_team', 'ix_war_room_task_team_team_id'):
        op.create_index('ix_war_room_task_team_team_id', 'war_room_task_team', ['team_id'])


def downgrade():
    if _has_table('war_room_task_team'):
        op.drop_table('war_room_task_team')
