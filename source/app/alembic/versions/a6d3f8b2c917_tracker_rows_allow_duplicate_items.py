#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Tracker rows: an item can have several rows in a manual tracker.

- `uq_case_tracker_row_item` and `uq_war_room_tracker_row_item` are
  dropped for the plain indexes `ix_case_tracker_row_item` and
  `ix_war_room_tracker_row_item` on the same columns. A mirror still
  holds at most one row per item: the business layer locks the tracker
  before creating one.
- `label` of an item row is now the custom label of the entry (NULL:
  the item's own label). Nothing to migrate, it was always NULL.

The downgrade does not restore the constraints: it would fail on the
duplicates created meanwhile.

Idempotent: `db.create_all()` runs before the migrations on a fresh
install and already creates the indexes.

Revision ID: a6d3f8b2c917
Revises: c4e9a2f7d1b5
Create Date: 2026-11-10
"""
from alembic import op
from sqlalchemy import inspect

from app.alembic.alembic_utils import _has_table
from app.alembic.alembic_utils import index_exists


revision = 'a6d3f8b2c917'
down_revision = 'c4e9a2f7d1b5'
branch_labels = None
depends_on = None

#: `(table, dropped unique constraint, created index)`.
_TABLES = (
    ('case_tracker_row', 'uq_case_tracker_row_item', 'ix_case_tracker_row_item'),
    ('war_room_tracker_row', 'uq_war_room_tracker_row_item', 'ix_war_room_tracker_row_item'),
)


def _has_unique_constraint(table, name):
    constraints = inspect(op.get_bind()).get_unique_constraints(table)
    return any(constraint['name'] == name for constraint in constraints)


def upgrade():
    for table, constraint, index in _TABLES:
        if not _has_table(table):
            continue
        if _has_unique_constraint(table, constraint):
            op.drop_constraint(constraint, table, type_='unique')
        if not index_exists(table, index):
            op.create_index(index, table, ['tracker_id', 'item_type', 'item_id'])


def downgrade():
    for table, _, index in _TABLES:
        if index_exists(table, index):
            op.drop_index(index, table_name=table)
