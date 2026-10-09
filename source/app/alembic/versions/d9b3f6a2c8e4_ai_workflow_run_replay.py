"""AI workflows — replayed events and node tests.

`ai_workflow_run.replayed_from_step_id`: the step whose event a run
replays (from that step's node, with the context the step saw); no
foreign key, the step table already references the run table. An index
on `ai_workflow_run_step.node_id` serves the per-node event list.

Idempotent via `_table_has_column` / `index_exists`: `db.create_all()`
runs before the migrations on a fresh install.

Revision ID: d9b3f6a2c8e4
Revises: c4a8e1f7b2d3
Create Date: 2026-10-09 10:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

from app.alembic.alembic_utils import _table_has_column
from app.alembic.alembic_utils import index_exists


revision = 'd9b3f6a2c8e4'
down_revision = 'c4a8e1f7b2d3'
branch_labels = None
depends_on = None

_RUN = 'ai_workflow_run'
_STEP = 'ai_workflow_run_step'
_COLUMN = 'replayed_from_step_id'
_STEP_NODE_INDEX = 'ix_ai_workflow_run_step_node_id'


def upgrade():
    if not _table_has_column(_RUN, _COLUMN):
        op.add_column(_RUN, sa.Column(_COLUMN, sa.BigInteger(), nullable=True))
    if not index_exists(_STEP, _STEP_NODE_INDEX):
        op.create_index(_STEP_NODE_INDEX, _STEP, ['node_id', 'run_id'])


def downgrade():
    if index_exists(_STEP, _STEP_NODE_INDEX):
        op.drop_index(_STEP_NODE_INDEX, table_name=_STEP)
    if _table_has_column(_RUN, _COLUMN):
        op.drop_column(_RUN, _COLUMN)
