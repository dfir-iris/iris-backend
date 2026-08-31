#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Fix user_activity.war_room_id FK to ON DELETE SET NULL.

`d1e2f3a4b5c6` added the column with a bare `sa.ForeignKey(...)`, so
Postgres created `user_activity_war_room_id_fkey` with NO ACTION.
Deleting a war room that had any tracked activity therefore raised
IntegrityError (ForeignKeyViolation) — and `war_room_create` itself
writes such a row, so every room was undeletable once created.

SET NULL rather than CASCADE: the audit trail should outlive the room
it refers to. `war_room_delete` already assumes this (it logs the
delete with war_room_id omitted so the entry doesn't dangle).

Idempotent: the constraint is looked up in pg_constraint by table +
column, and rebuilt only when its delete action isn't already SET NULL.

Revision ID: e3f1b8c2d094
Revises: c7f3a91d0e42
Create Date: 2026-08-31
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

from app.alembic.alembic_utils import _table_has_column


revision = 'e3f1b8c2d094'
down_revision = 'c7f3a91d0e42'
branch_labels = None
depends_on = None


# pg_constraint.confdeltype codes: 'a' NO ACTION, 'r' RESTRICT,
# 'c' CASCADE, 'n' SET NULL, 'd' SET DEFAULT.
_FIND_FK = text("""
    SELECT con.conname, con.confdeltype
      FROM pg_constraint con
      JOIN pg_class rel ON rel.oid = con.conrelid
      JOIN pg_attribute att
        ON att.attrelid = con.conrelid
       AND att.attnum = con.conkey[1]
     WHERE con.contype = 'f'
       AND rel.relname = 'user_activity'
       AND att.attname = 'war_room_id'
       AND array_length(con.conkey, 1) = 1
""")


def _current_fk():
    """Return (name, confdeltype) for the war_room_id FK, or None."""
    return op.get_bind().execute(_FIND_FK).fetchone()


def _rebuild(ondelete):
    existing = _current_fk()
    if existing is not None:
        op.drop_constraint(existing[0], 'user_activity', type_='foreignkey')
    op.create_foreign_key(
        'user_activity_war_room_id_fkey',
        'user_activity',
        'war_room',
        ['war_room_id'],
        ['war_room_id'],
        ondelete=ondelete,
    )


def upgrade():
    if not _table_has_column('user_activity', 'war_room_id'):
        return
    existing = _current_fk()
    if existing is not None and existing[1] == 'n':
        return
    _rebuild('SET NULL')


def downgrade():
    if not _table_has_column('user_activity', 'war_room_id'):
        return
    existing = _current_fk()
    if existing is not None and existing[1] == 'a':
        return
    _rebuild(None)
