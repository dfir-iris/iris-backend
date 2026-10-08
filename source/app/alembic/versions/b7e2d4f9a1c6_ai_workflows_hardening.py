"""AI workflows — security and load hardening.

- `ai_workflow`: separate encrypted HMAC signing secret for inbound
  webhooks; suppressed triggers are counted instead of stored as runs.
- `ai_workflow_run`: the owner at run start (visibility survives an
  ownership transfer), the API-key scope mask of the triggering
  credential, the keystore names used so far, and a requeue stamp.
- `ai_workflow_inbound_event`: hash of an accepted signature, for replay
  rejection.
- Indexes on every SET NULL foreign key (the retention prune otherwise
  scans each child table once per deleted row) and a partial index for
  the hourly cap, which ignores skipped runs.

Idempotent via `_table_has_column` / `index_exists`: `db.create_all()`
runs before the migrations on a fresh install.

Revision ID: b7e2d4f9a1c6
Revises: f3c7a1d9e2b4
Create Date: 2026-10-08 20:00:00.000000
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.alembic.alembic_utils import _has_table
from app.alembic.alembic_utils import _table_has_column
from app.alembic.alembic_utils import index_exists


revision = 'b7e2d4f9a1c6'
down_revision = 'f3c7a1d9e2b4'
branch_labels = None
depends_on = None


def _columns():
    jsonb = postgresql.JSONB(astext_type=sa.Text())
    return (
        ('ai_workflow', sa.Column('inbound_signing_secret', sa.Text(), nullable=True)),
        ('ai_workflow', sa.Column('skipped_count', sa.BigInteger(), nullable=False, server_default=sa.text('0'))),
        ('ai_workflow', sa.Column('last_skip_reason', sa.String(length=64), nullable=True)),
        ('ai_workflow', sa.Column('last_skipped_at', sa.DateTime(), nullable=True)),
        ('ai_workflow_run', sa.Column('owner_id', sa.BigInteger(), sa.ForeignKey('user.id', ondelete='SET NULL'),
                                      nullable=True)),
        ('ai_workflow_run', sa.Column('scope_mask', sa.BigInteger(), nullable=True)),
        ('ai_workflow_run', sa.Column('used_key_names', jsonb, nullable=False,
                                      server_default=sa.text("'[]'::jsonb"))),
        ('ai_workflow_run', sa.Column('requeued_at', sa.DateTime(), nullable=True)),
        ('ai_workflow_inbound_event', sa.Column('signature_sha256', sa.String(length=64), nullable=True)),
    )


_INDEXES = (
    ('ai_workflow_run', 'ix_ai_workflow_run_parent', ['parent_run_id']),
    ('ai_suggestion', 'ix_ai_suggestion_step', ['step_id']),
    ('ai_suggestion', 'ix_ai_suggestion_wait', ['wait_id']),
    ('ai_workflow_tool_call', 'ix_ai_workflow_tool_call_step', ['step_id']),
    ('ai_workflow_tool_call', 'ix_ai_workflow_tool_call_suggestion', ['suggestion_id']),
    ('ai_workflow_tool_call', 'ix_ai_workflow_tool_call_created', ['created_at']),
    ('ai_workflow_llm_call', 'ix_ai_workflow_llm_call_step', ['step_id']),
    ('ai_workflow_inbound_event', 'ix_ai_workflow_inbound_event_workflow', ['workflow_id', 'created_at']),
    ('ai_workflow_inbound_event', 'ix_ai_workflow_inbound_event_wait', ['wait_id']),
    ('ai_workflow_inbound_event', 'ix_ai_workflow_inbound_event_run', ['run_id']),
    ('ai_workflow_inbound_event', 'ix_ai_workflow_inbound_event_signature', ['signature_sha256']),
)

_COUNTED_INDEX = 'ix_ai_workflow_run_workflow_started_counted'


def upgrade():
    for table, column in _columns():
        if _has_table(table) and not _table_has_column(table, column.name):
            op.add_column(table, column)

    for table, name, columns in _INDEXES:
        if _has_table(table) and not index_exists(table, name):
            op.create_index(name, table, columns)

    if _has_table('ai_workflow_run') and not index_exists('ai_workflow_run', _COUNTED_INDEX):
        op.create_index(_COUNTED_INDEX, 'ai_workflow_run', ['workflow_id', 'started_at'],
                        postgresql_where=sa.text("status <> 'skipped'"))


def downgrade():
    for table, name, _ in (*_INDEXES, ('ai_workflow_run', _COUNTED_INDEX, None)):
        if _has_table(table) and index_exists(table, name):
            op.drop_index(name, table_name=table)

    for table, column in reversed(_columns()):
        if _has_table(table) and _table_has_column(table, column.name):
            op.drop_column(table, column.name)
