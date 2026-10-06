"""War room — asset stages, decision register, scope staging, note sharing, task fan-out.

- `asset_stage`: org-wide stage taxonomy, seeded with the default
  incident stages only when the table is empty (so stages an
  administrator deleted are never re-created on the next boot).
- `case_assets.stage_*` + `case_asset_stage_history`: current stage of
  a case asset and its transition log.
- `war_room_decision` (+ approver / case / asset link tables): the
  decision register, with a target date & time.
- `war_room_staged_object`: assets / IOCs staged in a war room before
  being pushed to cases.
- `war_room_note_share` (+ case link) and `notes.mirror_*` /
  `note_directory.mirror_war_room_id`: war-room notes mirrored into
  cases as read-only notes.
- `war_room_task_case_link`: war-room task fanned out to case tasks.
- `war_room.sitrep_cadence_minutes` / `sitrep_reminder_minutes`.

Every new column is nullable (or has a server default), so existing
rows are untouched. Idempotent via `_has_table` / `_table_has_column`
/ `index_exists`: `db.create_all()` runs before the migrations on a
fresh install and already creates the new tables (but never adds
columns to existing tables).

Revision ID: e8b1c4d2a7f9
Revises: d2a6b8c1e374
Create Date: 2026-10-06 10:00:00.000000
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.alembic.alembic_utils import _has_table
from app.alembic.alembic_utils import _table_has_column
from app.alembic.alembic_utils import index_exists


revision = 'e8b1c4d2a7f9'
down_revision = 'd2a6b8c1e374'
branch_labels = None
depends_on = None


_DEFAULT_STAGES = (
    # name, description, color, icon, kind, sort_order, requires_reason
    ('Identified', 'Asset is known to be in scope of the incident', 'slate', 'scan-search', 'progress', 0, False),
    ('Isolated', 'Asset is contained (network isolation, account disabled, …)', 'orange', 'unplug', 'progress', 1,
     False),
    ('Analysed', 'Forensic analysis of the asset is complete', 'blue', 'microscope', 'progress', 2, False),
    ('Patched', 'Root cause is fixed (patch, configuration, credentials reset)', 'violet', 'wrench', 'progress', 3,
     False),
    ('Restored', 'Asset is back in normal operation', 'emerald', 'circle-check', 'done', 4, False),
    ('Unpatched', 'Accepted exception: the asset cannot be fixed for now', 'amber', 'shield-off', 'exception', 5,
     True),
    ('Blocked', 'Work on the asset is blocked', 'red', 'octagon-x', 'exception', 6, True),
)


def _create_index(name, table, columns):
    if not index_exists(table, name):
        op.create_index(name, table, columns)


def _add_column(table, column):
    if not _table_has_column(table, column.name):
        op.add_column(table, column)


def _fk_exists(table, name):
    bind = op.get_bind()
    return any(fk.get('name') == name for fk in sa.inspect(bind).get_foreign_keys(table))


def _create_fk(name, source, referent, local_cols, remote_cols, ondelete=None):
    if not _fk_exists(source, name):
        op.create_foreign_key(name, source, referent, local_cols, remote_cols, ondelete=ondelete)


def _upgrade_asset_stages():
    if not _has_table('asset_stage'):
        op.create_table(
            'asset_stage',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('name', sa.String(length=64), nullable=False, unique=True),
            sa.Column('description', sa.Text(), nullable=True),
            sa.Column('color', sa.String(length=16), nullable=False, server_default=sa.text("'slate'")),
            sa.Column('icon', sa.String(length=64), nullable=True),
            sa.Column('kind', sa.String(length=16), nullable=False, server_default=sa.text("'progress'")),
            sa.Column('sort_order', sa.Integer(), nullable=False, server_default=sa.text('0')),
            sa.Column('requires_reason', sa.Boolean(), nullable=False, server_default=sa.text('false')),
            sa.Column('requires_decision', sa.Boolean(), nullable=False, server_default=sa.text('false')),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.CheckConstraint("kind IN ('progress', 'done', 'exception')", name='ck_asset_stage_kind'),
        )

    bind = op.get_bind()
    count = bind.execute(sa.text('SELECT COUNT(*) FROM asset_stage')).scalar()
    if not count:
        for name, description, color, icon, kind, sort_order, requires_reason in _DEFAULT_STAGES:
            bind.execute(
                sa.text('INSERT INTO asset_stage (name, description, color, icon, kind, sort_order, '
                        'requires_reason, requires_decision) '
                        'VALUES (:name, :description, :color, :icon, :kind, :sort_order, '
                        ':requires_reason, false)'),
                {'name': name, 'description': description, 'color': color, 'icon': icon, 'kind': kind,
                 'sort_order': sort_order, 'requires_reason': requires_reason}
            )


def _upgrade_decisions():
    if not _has_table('war_room_decision'):
        op.create_table(
            'war_room_decision',
            sa.Column('decision_id', sa.BigInteger(), primary_key=True),
            sa.Column('war_room_id', sa.BigInteger(),
                      sa.ForeignKey('war_room.war_room_id', ondelete='CASCADE'), nullable=False),
            sa.Column('number', sa.Integer(), nullable=False),
            sa.Column('title', sa.String(length=256), nullable=False),
            sa.Column('rationale', sa.Text(), nullable=True),
            sa.Column('status', sa.String(length=16), nullable=False, server_default=sa.text("'proposed'")),
            sa.Column('target_at', sa.DateTime(), nullable=True),
            sa.Column('owner_id', sa.BigInteger(), sa.ForeignKey('user.id', ondelete='SET NULL'), nullable=True),
            sa.Column('supersedes_id', sa.BigInteger(),
                      sa.ForeignKey('war_room_decision.decision_id', ondelete='SET NULL'), nullable=True),
            sa.Column('chat_message_id', sa.BigInteger(),
                      sa.ForeignKey('war_room_chat_message.message_id', ondelete='SET NULL'), nullable=True),
            sa.Column('decided_at', sa.DateTime(), nullable=True),
            sa.Column('decided_by_id', sa.BigInteger(),
                      sa.ForeignKey('user.id', ondelete='SET NULL'), nullable=True),
            sa.Column('implemented_at', sa.DateTime(), nullable=True),
            sa.Column('implemented_by_id', sa.BigInteger(),
                      sa.ForeignKey('user.id', ondelete='SET NULL'), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column('created_by_id', sa.BigInteger(),
                      sa.ForeignKey('user.id', ondelete='SET NULL'), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint('war_room_id', 'number', name='uq_war_room_decision_number'),
            sa.CheckConstraint("status IN ('proposed', 'approved', 'rejected', 'superseded')",
                               name='ck_war_room_decision_status'),
        )
    _create_index('ix_war_room_decision_war_room_id', 'war_room_decision', ['war_room_id'])

    if not _has_table('war_room_decision_approver'):
        op.create_table(
            'war_room_decision_approver',
            sa.Column('decision_id', sa.BigInteger(),
                      sa.ForeignKey('war_room_decision.decision_id', ondelete='CASCADE'), primary_key=True),
            sa.Column('user_id', sa.BigInteger(),
                      sa.ForeignKey('user.id', ondelete='CASCADE'), primary_key=True),
            sa.Column('verdict', sa.String(length=16), nullable=True),
            sa.Column('comment', sa.Text(), nullable=True),
            sa.Column('responded_at', sa.DateTime(), nullable=True),
        )

    if not _has_table('war_room_decision_case'):
        op.create_table(
            'war_room_decision_case',
            sa.Column('decision_id', sa.BigInteger(),
                      sa.ForeignKey('war_room_decision.decision_id', ondelete='CASCADE'), primary_key=True),
            sa.Column('case_id', sa.BigInteger(),
                      sa.ForeignKey('cases.case_id', ondelete='CASCADE'), primary_key=True),
        )
    _create_index('ix_war_room_decision_case_case_id', 'war_room_decision_case', ['case_id'])

    if not _has_table('war_room_decision_asset'):
        op.create_table(
            'war_room_decision_asset',
            sa.Column('decision_id', sa.BigInteger(),
                      sa.ForeignKey('war_room_decision.decision_id', ondelete='CASCADE'), primary_key=True),
            sa.Column('asset_id', sa.BigInteger(),
                      sa.ForeignKey('case_assets.asset_id', ondelete='CASCADE'), primary_key=True),
        )
    _create_index('ix_war_room_decision_asset_asset_id', 'war_room_decision_asset', ['asset_id'])


def _upgrade_case_asset_stage():
    _add_column('case_assets', sa.Column('stage_id', sa.Integer(), nullable=True))
    _add_column('case_assets', sa.Column('stage_reason', sa.Text(), nullable=True))
    _add_column('case_assets', sa.Column('stage_decision_id', sa.BigInteger(), nullable=True))
    _add_column('case_assets', sa.Column('stage_updated_at', sa.DateTime(), nullable=True))
    _add_column('case_assets', sa.Column('stage_updated_by_id', sa.BigInteger(), nullable=True))
    _create_fk('case_assets_stage_id_fkey', 'case_assets', 'asset_stage', ['stage_id'], ['id'])
    _create_fk('case_assets_stage_decision_id_fkey', 'case_assets', 'war_room_decision',
               ['stage_decision_id'], ['decision_id'], ondelete='SET NULL')
    _create_fk('case_assets_stage_updated_by_id_fkey', 'case_assets', 'user',
               ['stage_updated_by_id'], ['id'], ondelete='SET NULL')
    _create_index('ix_case_assets_stage_id', 'case_assets', ['stage_id'])

    if not _has_table('case_asset_stage_history'):
        op.create_table(
            'case_asset_stage_history',
            sa.Column('id', sa.BigInteger(), primary_key=True),
            sa.Column('asset_id', sa.BigInteger(),
                      sa.ForeignKey('case_assets.asset_id', ondelete='CASCADE'), nullable=False),
            sa.Column('case_id', sa.BigInteger(),
                      sa.ForeignKey('cases.case_id', ondelete='CASCADE'), nullable=False),
            sa.Column('from_stage_id', sa.Integer(),
                      sa.ForeignKey('asset_stage.id', ondelete='SET NULL'), nullable=True),
            sa.Column('from_stage_name', sa.String(length=64), nullable=True),
            sa.Column('to_stage_id', sa.Integer(),
                      sa.ForeignKey('asset_stage.id', ondelete='SET NULL'), nullable=True),
            sa.Column('to_stage_name', sa.String(length=64), nullable=True),
            sa.Column('reason', sa.Text(), nullable=True),
            sa.Column('decision_id', sa.BigInteger(),
                      sa.ForeignKey('war_room_decision.decision_id', ondelete='SET NULL'), nullable=True),
            sa.Column('war_room_id', sa.BigInteger(),
                      sa.ForeignKey('war_room.war_room_id', ondelete='SET NULL'), nullable=True),
            sa.Column('changed_by_id', sa.BigInteger(),
                      sa.ForeignKey('user.id', ondelete='SET NULL'), nullable=True),
            sa.Column('changed_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
    _create_index('ix_case_asset_stage_history_asset_id', 'case_asset_stage_history', ['asset_id'])
    _create_index('ix_case_asset_stage_history_case_id', 'case_asset_stage_history', ['case_id'])
    _create_index('ix_case_asset_stage_history_changed_at', 'case_asset_stage_history', ['changed_at'])


def _upgrade_staging():
    if not _has_table('war_room_staged_object'):
        op.create_table(
            'war_room_staged_object',
            sa.Column('id', sa.BigInteger(), primary_key=True),
            sa.Column('war_room_id', sa.BigInteger(),
                      sa.ForeignKey('war_room.war_room_id', ondelete='CASCADE'), nullable=False),
            sa.Column('object_type', sa.String(length=8), nullable=False),
            sa.Column('payload', postgresql.JSONB(), nullable=False),
            sa.Column('proposed_case_ids', postgresql.JSONB(), nullable=True),
            sa.Column('note', sa.Text(), nullable=True),
            sa.Column('source_message_id', sa.BigInteger(),
                      sa.ForeignKey('war_room_chat_message.message_id', ondelete='SET NULL'), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column('created_by_id', sa.BigInteger(),
                      sa.ForeignKey('user.id', ondelete='SET NULL'), nullable=True),
            sa.CheckConstraint("object_type IN ('asset', 'ioc')", name='ck_war_room_staged_object_type'),
        )
    _create_index('ix_war_room_staged_object_war_room_id', 'war_room_staged_object', ['war_room_id'])


def _upgrade_note_sharing():
    if not _has_table('war_room_note_share'):
        op.create_table(
            'war_room_note_share',
            sa.Column('share_id', sa.BigInteger(), primary_key=True),
            sa.Column('war_room_id', sa.BigInteger(),
                      sa.ForeignKey('war_room.war_room_id', ondelete='CASCADE'), nullable=False),
            sa.Column('note_id', sa.BigInteger(),
                      sa.ForeignKey('war_room_note.note_id', ondelete='CASCADE'), nullable=True),
            sa.Column('folder_id', sa.BigInteger(),
                      sa.ForeignKey('war_room_note_folder.id', ondelete='CASCADE'), nullable=True),
            sa.Column('scope', sa.String(length=8), nullable=False, server_default=sa.text("'cases'")),
            sa.Column('include_future', sa.Boolean(), nullable=False, server_default=sa.text('false')),
            sa.Column('delivery', sa.String(length=8), nullable=False, server_default=sa.text("'mirror'")),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column('created_by_id', sa.BigInteger(),
                      sa.ForeignKey('user.id', ondelete='SET NULL'), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.CheckConstraint('(note_id IS NULL) <> (folder_id IS NULL)', name='ck_war_room_note_share_target'),
            sa.CheckConstraint("scope IN ('all', 'cases')", name='ck_war_room_note_share_scope'),
            sa.CheckConstraint("delivery IN ('mirror', 'copy')", name='ck_war_room_note_share_delivery'),
        )
    _create_index('ix_war_room_note_share_war_room_id', 'war_room_note_share', ['war_room_id'])
    _create_index('ix_war_room_note_share_note_id', 'war_room_note_share', ['note_id'])
    _create_index('ix_war_room_note_share_folder_id', 'war_room_note_share', ['folder_id'])

    if not _has_table('war_room_note_share_case'):
        op.create_table(
            'war_room_note_share_case',
            sa.Column('share_id', sa.BigInteger(),
                      sa.ForeignKey('war_room_note_share.share_id', ondelete='CASCADE'), primary_key=True),
            sa.Column('case_id', sa.BigInteger(),
                      sa.ForeignKey('cases.case_id', ondelete='CASCADE'), primary_key=True),
        )
    _create_index('ix_war_room_note_share_case_case_id', 'war_room_note_share_case', ['case_id'])

    _add_column('notes', sa.Column('mirror_source_note_id', sa.BigInteger(), nullable=True))
    _add_column('notes', sa.Column('mirror_war_room_id', sa.BigInteger(), nullable=True))
    _create_fk('notes_mirror_source_note_id_fkey', 'notes', 'war_room_note',
               ['mirror_source_note_id'], ['note_id'], ondelete='SET NULL')
    _create_fk('notes_mirror_war_room_id_fkey', 'notes', 'war_room',
               ['mirror_war_room_id'], ['war_room_id'], ondelete='SET NULL')
    _create_index('ix_notes_mirror_source_note_id', 'notes', ['mirror_source_note_id'])

    _add_column('note_directory', sa.Column('mirror_war_room_id', sa.BigInteger(), nullable=True))
    _create_fk('note_directory_mirror_war_room_id_fkey', 'note_directory', 'war_room',
               ['mirror_war_room_id'], ['war_room_id'], ondelete='SET NULL')


def _upgrade_task_fan_out():
    if not _has_table('war_room_task_case_link'):
        op.create_table(
            'war_room_task_case_link',
            sa.Column('id', sa.BigInteger(), primary_key=True),
            sa.Column('task_id', sa.BigInteger(),
                      sa.ForeignKey('war_room_task.task_id', ondelete='CASCADE'), nullable=False),
            sa.Column('case_id', sa.BigInteger(),
                      sa.ForeignKey('cases.case_id', ondelete='CASCADE'), nullable=False),
            sa.Column('case_task_id', sa.BigInteger(),
                      sa.ForeignKey('case_tasks.id', ondelete='CASCADE'), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column('created_by_id', sa.BigInteger(),
                      sa.ForeignKey('user.id', ondelete='SET NULL'), nullable=True),
            sa.UniqueConstraint('task_id', 'case_id', name='uq_war_room_task_case_link'),
        )
    _create_index('ix_war_room_task_case_link_task_id', 'war_room_task_case_link', ['task_id'])
    _create_index('ix_war_room_task_case_link_case_id', 'war_room_task_case_link', ['case_id'])
    _create_index('ix_war_room_task_case_link_case_task_id', 'war_room_task_case_link', ['case_task_id'])

    _add_column('war_room', sa.Column('sitrep_cadence_minutes', sa.Integer(), nullable=True))
    _add_column('war_room', sa.Column('sitrep_reminder_minutes', sa.Integer(), nullable=True))


def upgrade():
    _upgrade_asset_stages()
    _upgrade_decisions()
    _upgrade_case_asset_stage()
    _upgrade_staging()
    _upgrade_note_sharing()
    _upgrade_task_fan_out()


def _drop_column(table, column):
    if _table_has_column(table, column):
        op.drop_column(table, column)


def _drop_table(table):
    if _has_table(table):
        op.drop_table(table)


def downgrade():
    _drop_column('war_room', 'sitrep_reminder_minutes')
    _drop_column('war_room', 'sitrep_cadence_minutes')
    _drop_table('war_room_task_case_link')

    _drop_column('note_directory', 'mirror_war_room_id')
    _drop_column('notes', 'mirror_war_room_id')
    _drop_column('notes', 'mirror_source_note_id')
    _drop_table('war_room_note_share_case')
    _drop_table('war_room_note_share')

    _drop_table('war_room_staged_object')

    _drop_table('case_asset_stage_history')
    _drop_column('case_assets', 'stage_updated_by_id')
    _drop_column('case_assets', 'stage_updated_at')
    _drop_column('case_assets', 'stage_decision_id')
    _drop_column('case_assets', 'stage_reason')
    _drop_column('case_assets', 'stage_id')

    _drop_table('war_room_decision_asset')
    _drop_table('war_room_decision_case')
    _drop_table('war_room_decision_approver')
    _drop_table('war_room_decision')

    _drop_table('asset_stage')
