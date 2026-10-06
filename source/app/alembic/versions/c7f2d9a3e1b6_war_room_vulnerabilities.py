"""Vulnerabilities tracked by a war room; vulnerability read / create permissions.

- `war_room_vulnerability`: a catalogue entry tracked by a war room,
  independently of the asset findings of its cases. Both foreign keys
  cascade: deleting the room or the catalogue entry drops the tracking.
- `vulnerabilities_read` (0x20000000) and `vulnerabilities_create`
  (0x40000000) permissions. On upgrade, `vulnerabilities_read` goes to
  the groups holding `server_administrator` or `standard_user`, so
  existing users keep seeing the data, but `vulnerabilities_create` only
  to the `server_administrator` groups: adding catalogue entries and
  findings is opt-in for the other groups. `post_init` only seeds the
  mask of a group on the boot that creates it.

Idempotent via `_has_table` / `index_exists`: `db.create_all()` runs
before the migrations on a fresh install and already creates the table.

Revision ID: c7f2d9a3e1b6
Revises: b5e8c1d4a7f2
Create Date: 2026-10-06 21:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

from app.alembic.alembic_utils import _has_table
from app.alembic.alembic_utils import index_exists


revision = 'c7f2d9a3e1b6'
down_revision = 'b5e8c1d4a7f2'
branch_labels = None
depends_on = None


_PERM_STANDARD_USER = 0x1
_PERM_SERVER_ADMINISTRATOR = 0x2
_PERM_VULNERABILITIES_READ = 0x20000000
_PERM_VULNERABILITIES_CREATE = 0x40000000
_PERMS_NEW = _PERM_VULNERABILITIES_READ | _PERM_VULNERABILITIES_CREATE


def upgrade():
    if not _has_table('war_room_vulnerability'):
        op.create_table(
            'war_room_vulnerability',
            sa.Column('war_room_id', sa.BigInteger(),
                      sa.ForeignKey('war_room.war_room_id', ondelete='CASCADE'), primary_key=True),
            sa.Column('vulnerability_id', sa.BigInteger(),
                      sa.ForeignKey('vulnerability.vulnerability_id', ondelete='CASCADE'), primary_key=True),
            sa.Column('note', sa.Text(), nullable=True),
            sa.Column('added_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
            sa.Column('added_by_id', sa.BigInteger(),
                      sa.ForeignKey('user.id', ondelete='SET NULL'), nullable=True),
        )
    if not index_exists('war_room_vulnerability', 'idx_war_room_vulnerability_vulnerability'):
        op.create_index('idx_war_room_vulnerability_vulnerability', 'war_room_vulnerability',
                        ['vulnerability_id'])

    if _has_table('groups'):
        op.execute(sa.text(
            f'UPDATE groups SET group_permissions = group_permissions | {_PERMS_NEW} '
            f'WHERE group_permissions & {_PERM_SERVER_ADMINISTRATOR} != 0'
        ))
        op.execute(sa.text(
            f'UPDATE groups SET group_permissions = group_permissions | {_PERM_VULNERABILITIES_READ} '
            f'WHERE group_permissions & {_PERM_SERVER_ADMINISTRATOR} = 0 '
            f'AND group_permissions & {_PERM_STANDARD_USER} != 0'
        ))


def downgrade():
    # Both bits are introduced by this revision (nothing below it knows
    # them), so clearing them on every group restores the previous state.
    if _has_table('groups'):
        op.execute(sa.text(
            f'UPDATE groups SET group_permissions = group_permissions & ~{_PERMS_NEW} '
            f'WHERE group_permissions & {_PERMS_NEW} != 0'
        ))
    if _has_table('war_room_vulnerability'):
        op.drop_table('war_room_vulnerability')
