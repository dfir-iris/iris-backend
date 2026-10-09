"""AI workflows — node test runs.

`ai_workflow_run.tested_node_id`: the node a test run executes on its
own (such runs are not events of the workflow). A revision of its own:
databases that already ran d9b3f6a2c8e4 before this column was added
there would never get it.

Idempotent via `_table_has_column`: `db.create_all()` runs before the
migrations on a fresh install.

Revision ID: e1f4b8c2a6d9
Revises: d9b3f6a2c8e4
Create Date: 2026-10-09 11:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

from app.alembic.alembic_utils import _table_has_column


revision = 'e1f4b8c2a6d9'
down_revision = 'd9b3f6a2c8e4'
branch_labels = None
depends_on = None

_RUN = 'ai_workflow_run'
_COLUMN = 'tested_node_id'


def upgrade():
    if not _table_has_column(_RUN, _COLUMN):
        op.add_column(_RUN, sa.Column(_COLUMN, sa.String(64), nullable=True))


def downgrade():
    if _table_has_column(_RUN, _COLUMN):
        op.drop_column(_RUN, _COLUMN)
