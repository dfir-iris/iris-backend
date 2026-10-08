"""AI workflows — workflows, runs, trace ledger, suggestions, keystore.

Adds the AI workflow tables (definition + version history, runs with
their step / tool call / LLM call ledger, waits, suggestions, inbound
webhook log) and the keystore. Grants the new `ai_workflows_read` /
`ai_workflows_write` permissions to every group holding
`server_administrator`, which is what `ac_get_mask_full_permissions()`
gives the administrators group on a fresh install.

The new `on_postload_ai_*` hooks are seeded by `create_safe_hooks` at
boot, after the migrations.

Idempotent via `_has_table` / `index_exists`: `db.create_all()` runs
before the migrations on a fresh install and creates every table.

Revision ID: f3c7a1d9e2b4
Revises: e5b9c2a7d1f3
Create Date: 2026-10-08 16:00:00.000000
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.alembic.alembic_utils import _has_table
from app.alembic.alembic_utils import index_exists


revision = 'f3c7a1d9e2b4'
down_revision = 'e5b9c2a7d1f3'
branch_labels = None
depends_on = None


_SERVER_ADMINISTRATOR = 0x2
_AI_WORKFLOWS_PERMISSIONS = 0x80000000 | 0x100000000

_TABLES = (
    'ai_workflow_inbound_event',
    'ai_keystore_entry',
    'ai_workflow_llm_call',
    'ai_workflow_tool_call',
    'ai_suggestion',
    'ai_workflow_wait',
    'ai_workflow_run_step',
    'ai_workflow_run',
    'ai_workflow_version',
    'ai_workflow',
)


def _user_fk(ondelete='SET NULL'):
    return sa.ForeignKey('user.id', ondelete=ondelete)


def _created_at():
    return sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now())


def _index(table, name, columns):
    if not index_exists(table, name):
        op.create_index(name, table, columns)


def upgrade():
    jsonb = postgresql.JSONB(astext_type=sa.Text())
    uuid = postgresql.UUID(as_uuid=True)

    if not _has_table('ai_workflow'):
        op.create_table(
            'ai_workflow',
            sa.Column('id', sa.BigInteger(), primary_key=True),
            sa.Column('uuid', uuid, nullable=False, unique=True),
            sa.Column('name', sa.String(length=255), nullable=False),
            sa.Column('description', sa.Text(), nullable=True),
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('false')),
            sa.Column('trigger_type', sa.String(length=16), nullable=False, server_default=sa.text("'manual'")),
            sa.Column('trigger_config', jsonb, nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column('customer_scope', jsonb, nullable=True),
            sa.Column('graph', jsonb, nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column('owner_id', sa.BigInteger(), _user_fk('RESTRICT'), nullable=False),
            sa.Column('write_tool_allowlist', jsonb, nullable=False, server_default=sa.text("'[]'::jsonb")),
            sa.Column('max_runs_per_hour', sa.Integer(), nullable=False, server_default=sa.text('60')),
            sa.Column('token_budget_per_run', sa.Integer(), nullable=False, server_default=sa.text('50000')),
            sa.Column('suggestion_audience', sa.String(length=16), nullable=False,
                      server_default=sa.text("'entity'")),
            sa.Column('version', sa.Integer(), nullable=False, server_default=sa.text('1')),
            sa.Column('inbound_token_hash', sa.String(length=64), nullable=True),
            sa.Column('last_fired_at', sa.DateTime(), nullable=True),
            sa.Column('created_by_id', sa.BigInteger(), _user_fk(), nullable=True),
            _created_at(),
            sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )

    if not _has_table('ai_workflow_version'):
        op.create_table(
            'ai_workflow_version',
            sa.Column('id', sa.BigInteger(), primary_key=True),
            sa.Column('workflow_id', sa.BigInteger(), sa.ForeignKey('ai_workflow.id', ondelete='CASCADE'),
                      nullable=False, index=True),
            sa.Column('version', sa.Integer(), nullable=False),
            sa.Column('snapshot', jsonb, nullable=False),
            sa.Column('note', sa.Text(), nullable=True),
            sa.Column('created_by_id', sa.BigInteger(), _user_fk(), nullable=True),
            _created_at(),
            sa.UniqueConstraint('workflow_id', 'version', name='uq_ai_workflow_version'),
        )

    if not _has_table('ai_workflow_run'):
        op.create_table(
            'ai_workflow_run',
            sa.Column('id', sa.BigInteger(), primary_key=True),
            sa.Column('uuid', uuid, nullable=False, unique=True),
            sa.Column('workflow_id', sa.BigInteger(), sa.ForeignKey('ai_workflow.id', ondelete='SET NULL'),
                      nullable=True),
            sa.Column('workflow_name', sa.String(length=255), nullable=False),
            sa.Column('workflow_version', sa.Integer(), nullable=False),
            sa.Column('definition_snapshot', jsonb, nullable=False),
            sa.Column('status', sa.String(length=16), nullable=False, server_default=sa.text("'running'")),
            sa.Column('trigger_type', sa.String(length=16), nullable=False),
            sa.Column('trigger_payload', jsonb, nullable=True),
            sa.Column('context', jsonb, nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column('pending_nodes', jsonb, nullable=False, server_default=sa.text("'[]'::jsonb")),
            sa.Column('waiting_node_id', sa.String(length=64), nullable=True),
            sa.Column('is_executing', sa.Boolean(), nullable=False, server_default=sa.text('false')),
            sa.Column('executing_since', sa.DateTime(), nullable=True),
            sa.Column('entity_type', sa.String(length=32), nullable=True),
            sa.Column('entity_id', sa.BigInteger(), nullable=True),
            sa.Column('sub_entity', jsonb, nullable=True),
            sa.Column('customer_id', sa.BigInteger(), sa.ForeignKey('client.client_id', ondelete='SET NULL'),
                      nullable=True),
            sa.Column('run_as_user_id', sa.BigInteger(), _user_fk(), nullable=True),
            sa.Column('triggered_by_user_id', sa.BigInteger(), _user_fk(), nullable=True),
            sa.Column('dedup_key', sa.String(length=255), nullable=True),
            sa.Column('chain_depth', sa.Integer(), nullable=False, server_default=sa.text('0')),
            sa.Column('parent_run_id', sa.BigInteger(), sa.ForeignKey('ai_workflow_run.id', ondelete='SET NULL'),
                      nullable=True),
            sa.Column('is_dry_run', sa.Boolean(), nullable=False, server_default=sa.text('false')),
            sa.Column('tokens_used', sa.Integer(), nullable=False, server_default=sa.text('0')),
            sa.Column('step_count', sa.Integer(), nullable=False, server_default=sa.text('0')),
            sa.Column('error', sa.Text(), nullable=True),
            sa.Column('started_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column('finished_at', sa.DateTime(), nullable=True),
        )
    _index('ai_workflow_run', 'ix_ai_workflow_run_workflow_started', ['workflow_id', 'started_at'])
    _index('ai_workflow_run', 'ix_ai_workflow_run_entity', ['entity_type', 'entity_id'])
    _index('ai_workflow_run', 'ix_ai_workflow_run_status', ['status'])
    _index('ai_workflow_run', 'ix_ai_workflow_run_dedup', ['workflow_id', 'dedup_key'])

    if not _has_table('ai_workflow_run_step'):
        op.create_table(
            'ai_workflow_run_step',
            sa.Column('id', sa.BigInteger(), primary_key=True),
            sa.Column('run_id', sa.BigInteger(), sa.ForeignKey('ai_workflow_run.id', ondelete='CASCADE'),
                      nullable=False),
            sa.Column('seq', sa.Integer(), nullable=False),
            sa.Column('node_id', sa.String(length=64), nullable=False),
            sa.Column('node_type', sa.String(length=32), nullable=False),
            sa.Column('node_label', sa.String(length=255), nullable=True),
            sa.Column('status', sa.String(length=16), nullable=False),
            sa.Column('input', jsonb, nullable=True),
            sa.Column('output', jsonb, nullable=True),
            sa.Column('port', sa.String(length=32), nullable=True),
            sa.Column('error', sa.Text(), nullable=True),
            sa.Column('tokens_used', sa.Integer(), nullable=False, server_default=sa.text('0')),
            sa.Column('started_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column('ended_at', sa.DateTime(), nullable=True),
        )
    _index('ai_workflow_run_step', 'ix_ai_workflow_run_step_run_seq', ['run_id', 'seq'])

    if not _has_table('ai_workflow_wait'):
        op.create_table(
            'ai_workflow_wait',
            sa.Column('id', sa.BigInteger(), primary_key=True),
            sa.Column('uuid', uuid, nullable=False, unique=True),
            sa.Column('run_id', sa.BigInteger(), sa.ForeignKey('ai_workflow_run.id', ondelete='CASCADE'),
                      nullable=False, index=True),
            sa.Column('node_id', sa.String(length=64), nullable=False),
            sa.Column('kind', sa.String(length=16), nullable=False),
            sa.Column('token_hash', sa.String(length=64), nullable=True),
            sa.Column('status', sa.String(length=16), nullable=False, server_default=sa.text("'pending'")),
            sa.Column('expires_at', sa.DateTime(), nullable=True),
            sa.Column('resolved_payload', jsonb, nullable=True),
            sa.Column('resolved_by_id', sa.BigInteger(), _user_fk(), nullable=True),
            sa.Column('resolved_at', sa.DateTime(), nullable=True),
            sa.Column('source_ip', sa.String(length=64), nullable=True),
            sa.Column('payload_sha256', sa.String(length=64), nullable=True),
            sa.Column('suggestion_id', sa.BigInteger(), nullable=True),
            _created_at(),
        )
    _index('ai_workflow_wait', 'ix_ai_workflow_wait_status_expires', ['status', 'expires_at'])

    if not _has_table('ai_suggestion'):
        op.create_table(
            'ai_suggestion',
            sa.Column('id', sa.BigInteger(), primary_key=True),
            sa.Column('uuid', uuid, nullable=False, unique=True),
            sa.Column('run_id', sa.BigInteger(), sa.ForeignKey('ai_workflow_run.id', ondelete='SET NULL'),
                      nullable=True),
            sa.Column('step_id', sa.BigInteger(), sa.ForeignKey('ai_workflow_run_step.id', ondelete='SET NULL'),
                      nullable=True),
            sa.Column('workflow_id', sa.BigInteger(), sa.ForeignKey('ai_workflow.id', ondelete='SET NULL'),
                      nullable=True),
            sa.Column('entity_type', sa.String(length=32), nullable=True),
            sa.Column('entity_id', sa.BigInteger(), nullable=True),
            sa.Column('sub_entity', jsonb, nullable=True),
            sa.Column('case_id', sa.BigInteger(), sa.ForeignKey('cases.case_id', ondelete='CASCADE'),
                      nullable=True),
            sa.Column('war_room_id', sa.BigInteger(), sa.ForeignKey('war_room.war_room_id', ondelete='CASCADE'),
                      nullable=True),
            sa.Column('alert_id', sa.BigInteger(), sa.ForeignKey('alerts.alert_id', ondelete='CASCADE'),
                      nullable=True),
            sa.Column('customer_id', sa.BigInteger(), sa.ForeignKey('client.client_id', ondelete='SET NULL'),
                      nullable=True),
            sa.Column('kind', sa.String(length=32), nullable=False, server_default=sa.text("'generic_action'")),
            sa.Column('title', sa.Text(), nullable=False),
            sa.Column('body', sa.Text(), nullable=True),
            sa.Column('proposed_action', jsonb, nullable=True),
            sa.Column('form_schema', jsonb, nullable=True),
            sa.Column('related_refs', jsonb, nullable=True),
            sa.Column('confidence', sa.Float(), nullable=True),
            sa.Column('severity', sa.String(length=16), nullable=True),
            sa.Column('status', sa.String(length=16), nullable=False, server_default=sa.text("'open'")),
            sa.Column('wait_id', sa.BigInteger(), sa.ForeignKey('ai_workflow_wait.id', ondelete='SET NULL'),
                      nullable=True),
            sa.Column('resolved_by_id', sa.BigInteger(), _user_fk(), nullable=True),
            sa.Column('resolved_at', sa.DateTime(), nullable=True),
            sa.Column('resolution_note', sa.Text(), nullable=True),
            sa.Column('answer', jsonb, nullable=True),
            sa.Column('result', jsonb, nullable=True),
            sa.Column('result_tool_call_id', sa.BigInteger(), nullable=True),
            sa.Column('audience_user_ids', jsonb, nullable=True),
            _created_at(),
        )
    _index('ai_suggestion', 'ix_ai_suggestion_entity_status', ['entity_type', 'entity_id', 'status'])
    _index('ai_suggestion', 'ix_ai_suggestion_run', ['run_id'])

    if not _has_table('ai_workflow_tool_call'):
        op.create_table(
            'ai_workflow_tool_call',
            sa.Column('id', sa.BigInteger(), primary_key=True),
            sa.Column('run_id', sa.BigInteger(), sa.ForeignKey('ai_workflow_run.id', ondelete='CASCADE'),
                      nullable=True),
            sa.Column('step_id', sa.BigInteger(), sa.ForeignKey('ai_workflow_run_step.id', ondelete='SET NULL'),
                      nullable=True),
            sa.Column('suggestion_id', sa.BigInteger(), sa.ForeignKey('ai_suggestion.id', ondelete='SET NULL'),
                      nullable=True),
            sa.Column('tool_name', sa.String(length=128), nullable=False),
            sa.Column('arguments', jsonb, nullable=True),
            sa.Column('result', jsonb, nullable=True),
            sa.Column('error', sa.Text(), nullable=True),
            sa.Column('classification', sa.String(length=16), nullable=False),
            sa.Column('execution_mode', sa.String(length=32), nullable=False),
            sa.Column('acting_user_id', sa.BigInteger(), _user_fk(), nullable=True),
            sa.Column('duration_ms', sa.Integer(), nullable=True),
            _created_at(),
        )
    _index('ai_workflow_tool_call', 'ix_ai_workflow_tool_call_run', ['run_id'])

    if not _has_table('ai_workflow_llm_call'):
        op.create_table(
            'ai_workflow_llm_call',
            sa.Column('id', sa.BigInteger(), primary_key=True),
            sa.Column('run_id', sa.BigInteger(), sa.ForeignKey('ai_workflow_run.id', ondelete='CASCADE'),
                      nullable=True),
            sa.Column('step_id', sa.BigInteger(), sa.ForeignKey('ai_workflow_run_step.id', ondelete='SET NULL'),
                      nullable=True),
            sa.Column('user_id', sa.BigInteger(), _user_fk(), nullable=True),
            sa.Column('provider', sa.String(length=32), nullable=True),
            sa.Column('model', sa.String(length=128), nullable=True),
            sa.Column('policy_id', sa.BigInteger(), nullable=True),
            sa.Column('restriction_level', sa.String(length=32), nullable=True),
            sa.Column('redacted', sa.Boolean(), nullable=False, server_default=sa.text('false')),
            sa.Column('request_snapshot', jsonb, nullable=True),
            sa.Column('response_snapshot', jsonb, nullable=True),
            sa.Column('prompt_tokens', sa.Integer(), nullable=False, server_default=sa.text('0')),
            sa.Column('completion_tokens', sa.Integer(), nullable=False, server_default=sa.text('0')),
            sa.Column('bytes_sent', sa.Integer(), nullable=False, server_default=sa.text('0')),
            sa.Column('error', sa.Text(), nullable=True),
            _created_at(),
        )
    _index('ai_workflow_llm_call', 'ix_ai_workflow_llm_call_run', ['run_id'])
    _index('ai_workflow_llm_call', 'ix_ai_workflow_llm_call_created', ['created_at'])

    if not _has_table('ai_keystore_entry'):
        op.create_table(
            'ai_keystore_entry',
            sa.Column('id', sa.BigInteger(), primary_key=True),
            sa.Column('name', sa.String(length=128), nullable=False),
            sa.Column('value', sa.Text(), nullable=False),
            sa.Column('is_secret', sa.Boolean(), nullable=False, server_default=sa.text('true')),
            sa.Column('description', sa.Text(), nullable=True),
            sa.Column('scope', sa.String(length=16), nullable=False, server_default=sa.text("'personal'")),
            sa.Column('owner_id', sa.BigInteger(), _user_fk('CASCADE'), nullable=True),
            sa.Column('allowed_group_ids', jsonb, nullable=False, server_default=sa.text("'[]'::jsonb")),
            sa.Column('allowed_hosts', jsonb, nullable=False, server_default=sa.text("'[]'::jsonb")),
            sa.Column('created_by_id', sa.BigInteger(), _user_fk(), nullable=True),
            _created_at(),
            sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column('last_used_at', sa.DateTime(), nullable=True),
        )
    _index('ai_keystore_entry', 'ix_ai_keystore_entry_name', ['name'])

    if not _has_table('ai_workflow_inbound_event'):
        op.create_table(
            'ai_workflow_inbound_event',
            sa.Column('id', sa.BigInteger(), primary_key=True),
            sa.Column('kind', sa.String(length=16), nullable=False),
            sa.Column('workflow_id', sa.BigInteger(), sa.ForeignKey('ai_workflow.id', ondelete='SET NULL'),
                      nullable=True),
            sa.Column('wait_id', sa.BigInteger(), sa.ForeignKey('ai_workflow_wait.id', ondelete='SET NULL'),
                      nullable=True),
            sa.Column('run_id', sa.BigInteger(), sa.ForeignKey('ai_workflow_run.id', ondelete='SET NULL'),
                      nullable=True),
            sa.Column('source_ip', sa.String(length=64), nullable=True),
            sa.Column('status', sa.String(length=16), nullable=False),
            sa.Column('reason', sa.Text(), nullable=True),
            sa.Column('payload_sha256', sa.String(length=64), nullable=True),
            sa.Column('payload_bytes', sa.Integer(), nullable=False, server_default=sa.text('0')),
            _created_at(),
        )
    _index('ai_workflow_inbound_event', 'ix_ai_workflow_inbound_event_created', ['created_at'])

    if _has_table('groups'):
        op.get_bind().execute(
            sa.text('UPDATE groups SET group_permissions = group_permissions | :perms '
                    'WHERE group_permissions & :admin != 0'),
            {'perms': _AI_WORKFLOWS_PERMISSIONS, 'admin': _SERVER_ADMINISTRATOR},
        )


def downgrade():
    for table in _TABLES:
        if _has_table(table):
            op.drop_table(table)
    if _has_table('groups'):
        op.get_bind().execute(
            sa.text('UPDATE groups SET group_permissions = group_permissions & ~CAST(:perms AS BIGINT)'),
            {'perms': _AI_WORKFLOWS_PERMISSIONS},
        )
