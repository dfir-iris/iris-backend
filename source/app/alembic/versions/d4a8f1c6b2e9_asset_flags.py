"""Asset flags replace asset stages.

An asset no longer walks a linear stage path: it carries any number of
status flags (Isolated, Patched, Credentials reset, Can't be patched…),
each change being written to the case "Asset status" timeline.

- `asset_flag`: org-wide flag taxonomy. Stage kinds map onto flag kinds
  (`progress` -> `status`, `done` and `exception` unchanged).
- `case_asset_flag`: the flags currently set on a case asset.
- `case_asset_flag_history`: append-only log of the flag changes.

Data conversion, only while the stage tables still exist:

- when the stage taxonomy is still one of the defaults seeded by
  e8b1c4d2a7f9 / f3c7a9e1b5d2 and nothing (current asset or history)
  references it, it is dropped and the default flags are seeded;
- otherwise every stage becomes a flag of the same name, the current
  stage of each asset becomes its single flag (reason, decision and
  author kept), and every stage transition A -> B becomes a "cleared A"
  and a "set B" history row. No timeline event is backfilled.

Then `case_assets.stage_*`, `case_asset_stage_history` and `asset_stage`
are dropped, and the `on_postload_war_room_scope_stage_update` hook is
renamed `on_postload_war_room_scope_flag_update` (module registrations
follow it).

Idempotent via `_has_table` / `_table_has_column`: `db.create_all()`
already creates the flag tables on a fresh install, and the stage
objects are only created by the earlier migrations of the same run.

Revision ID: d4a8f1c6b2e9
Revises: c7f2d9a3e1b6
Create Date: 2026-10-08 10:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

from app.alembic.alembic_utils import _has_table
from app.alembic.alembic_utils import _table_has_column
from app.alembic.alembic_utils import index_exists


revision = 'd4a8f1c6b2e9'
down_revision = 'c7f2d9a3e1b6'
branch_labels = None
depends_on = None


_DEFAULT_FLAGS = (
    # name, description, color, icon, kind, requires_reason
    ('Isolated', 'Asset is contained (network isolation, account disabled, …)', 'blue', 'unplug', 'status',
     False),
    ('Credentials reset', 'Credentials used on or by the asset were reset', 'teal', 'key-round', 'status', False),
    ('Patched', 'Root cause is fixed (patch, configuration change)', 'violet', 'wrench', 'status', False),
    ('Reimaged', 'Asset was reinstalled from a trusted image', 'indigo', 'hard-drive', 'status', False),
    ('Monitored', 'Asset is under reinforced monitoring', 'sky', 'eye', 'status', False),
    ('Restored', 'Asset is back in normal operation', 'emerald', 'circle-check', 'done', False),
    ("Can't be patched", 'Accepted exception: the asset cannot be fixed for now', 'amber', 'shield-off',
     'exception', True),
    ('Blocked', 'Work on the asset is blocked', 'red', 'octagon-x', 'exception', True),
)

# (name, kind) sets of the stage taxonomies seeded by e8b1c4d2a7f9 and
# tuned by f3c7a9e1b5d2.
_SEEDED_STAGES = {('Identified', 'progress'), ('Isolated', 'progress'), ('Patched', 'progress'),
                  ('Restored', 'done'), ('Unpatched', 'exception'), ('Blocked', 'exception')}
_SEEDED_STAGE_TAXONOMIES = (_SEEDED_STAGES, _SEEDED_STAGES | {('Analysed', 'progress')})

_STAGE_COLUMNS = ('stage_updated_by_id', 'stage_updated_at', 'stage_decision_id', 'stage_reason', 'stage_id')

_OLD_HOOK = 'on_postload_war_room_scope_stage_update'
_NEW_HOOK = 'on_postload_war_room_scope_flag_update'
_NEW_HOOK_DESCRIPTION = 'Triggered on asset flag change from the war room scope, after commit in DB'


def _create_index(name, table, columns):
    if not index_exists(table, name):
        op.create_index(name, table, columns)


def _create_flag_tables():
    if not _has_table('asset_flag'):
        op.create_table(
            'asset_flag',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('name', sa.String(length=64), nullable=False, unique=True),
            sa.Column('description', sa.Text(), nullable=True),
            sa.Column('color', sa.String(length=16), nullable=False, server_default=sa.text("'slate'")),
            sa.Column('icon', sa.String(length=64), nullable=True),
            sa.Column('kind', sa.String(length=16), nullable=False, server_default=sa.text("'status'")),
            sa.Column('sort_order', sa.Integer(), nullable=False, server_default=sa.text('0')),
            sa.Column('requires_reason', sa.Boolean(), nullable=False, server_default=sa.text('false')),
            sa.Column('requires_decision', sa.Boolean(), nullable=False, server_default=sa.text('false')),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.CheckConstraint("kind IN ('status', 'done', 'exception')", name='ck_asset_flag_kind'),
        )

    if not _has_table('case_asset_flag'):
        op.create_table(
            'case_asset_flag',
            sa.Column('asset_id', sa.BigInteger(),
                      sa.ForeignKey('case_assets.asset_id', ondelete='CASCADE'), primary_key=True),
            sa.Column('flag_id', sa.Integer(), sa.ForeignKey('asset_flag.id'), primary_key=True),
            sa.Column('case_id', sa.BigInteger(),
                      sa.ForeignKey('cases.case_id', ondelete='CASCADE'), nullable=False),
            sa.Column('reason', sa.Text(), nullable=True),
            sa.Column('decision_id', sa.BigInteger(),
                      sa.ForeignKey('war_room_decision.decision_id', ondelete='SET NULL'), nullable=True),
            sa.Column('event_id', sa.BigInteger(),
                      sa.ForeignKey('cases_events.event_id', ondelete='SET NULL'), nullable=True),
            sa.Column('set_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column('set_by_id', sa.BigInteger(),
                      sa.ForeignKey('user.id', ondelete='SET NULL'), nullable=True),
        )
    _create_index('ix_case_asset_flag_flag_id', 'case_asset_flag', ['flag_id'])
    _create_index('ix_case_asset_flag_case_id', 'case_asset_flag', ['case_id'])

    if not _has_table('case_asset_flag_history'):
        op.create_table(
            'case_asset_flag_history',
            sa.Column('id', sa.BigInteger(), primary_key=True),
            sa.Column('asset_id', sa.BigInteger(),
                      sa.ForeignKey('case_assets.asset_id', ondelete='CASCADE'), nullable=False),
            sa.Column('case_id', sa.BigInteger(),
                      sa.ForeignKey('cases.case_id', ondelete='CASCADE'), nullable=False),
            sa.Column('flag_id', sa.Integer(),
                      sa.ForeignKey('asset_flag.id', ondelete='SET NULL'), nullable=True),
            sa.Column('flag_name', sa.String(length=64), nullable=True),
            sa.Column('action', sa.String(length=16), nullable=False),
            sa.Column('reason', sa.Text(), nullable=True),
            sa.Column('decision_id', sa.BigInteger(),
                      sa.ForeignKey('war_room_decision.decision_id', ondelete='SET NULL'), nullable=True),
            sa.Column('war_room_id', sa.BigInteger(),
                      sa.ForeignKey('war_room.war_room_id', ondelete='SET NULL'), nullable=True),
            sa.Column('event_id', sa.BigInteger(),
                      sa.ForeignKey('cases_events.event_id', ondelete='SET NULL'), nullable=True),
            sa.Column('changed_by_id', sa.BigInteger(),
                      sa.ForeignKey('user.id', ondelete='SET NULL'), nullable=True),
            sa.Column('changed_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.CheckConstraint("action IN ('set', 'updated', 'cleared')",
                               name='ck_case_asset_flag_history_action'),
        )
    _create_index('ix_case_asset_flag_history_asset_id', 'case_asset_flag_history', ['asset_id'])
    _create_index('ix_case_asset_flag_history_case_id', 'case_asset_flag_history', ['case_id'])
    _create_index('ix_case_asset_flag_history_changed_at', 'case_asset_flag_history', ['changed_at'])


def _stages_are_referenced(bind):
    if _table_has_column('case_assets', 'stage_id'):
        if bind.execute(sa.text('SELECT 1 FROM case_assets WHERE stage_id IS NOT NULL LIMIT 1')).first():
            return True
    if _has_table('case_asset_stage_history'):
        if bind.execute(sa.text('SELECT 1 FROM case_asset_stage_history LIMIT 1')).first():
            return True
    return False


def _stages_are_seeded_default(bind):
    rows = bind.execute(sa.text('SELECT name, kind FROM asset_stage')).fetchall()
    taxonomy = {(row[0], row[1]) for row in rows}
    return len(rows) == len(taxonomy) and taxonomy in _SEEDED_STAGE_TAXONOMIES


def _copy_stages(bind):
    """Copy every stage into `asset_flag`. Returns {stage_id: flag_id}."""
    existing = {row[0].lower(): row[1] for row in bind.execute(sa.text('SELECT name, id FROM asset_flag'))}
    mapping = {}
    stages = bind.execute(sa.text(
        'SELECT id, name, description, color, icon, kind, sort_order, requires_reason, requires_decision, '
        'created_at FROM asset_stage ORDER BY sort_order, id')).fetchall()
    for stage in stages:
        flag_id = existing.get(stage.name.lower())
        if flag_id is None:
            flag_id = bind.execute(
                sa.text('INSERT INTO asset_flag (name, description, color, icon, kind, sort_order, '
                        'requires_reason, requires_decision, created_at) '
                        'VALUES (:name, :description, :color, :icon, :kind, :sort_order, '
                        ':requires_reason, :requires_decision, :created_at) RETURNING id'),
                {'name': stage.name, 'description': stage.description, 'color': stage.color,
                 'icon': stage.icon, 'kind': 'status' if stage.kind == 'progress' else stage.kind,
                 'sort_order': stage.sort_order, 'requires_reason': stage.requires_reason,
                 'requires_decision': stage.requires_decision, 'created_at': stage.created_at}
            ).scalar()
        mapping[stage.id] = flag_id
    return mapping


def _copy_current_stages(bind, mapping):
    if not _table_has_column('case_assets', 'stage_id'):
        return
    rows = bind.execute(sa.text(
        'SELECT asset_id, case_id, stage_id, stage_reason, stage_decision_id, stage_updated_at, '
        'stage_updated_by_id FROM case_assets WHERE stage_id IS NOT NULL AND case_id IS NOT NULL')).fetchall()
    for row in rows:
        flag_id = mapping.get(row.stage_id)
        if flag_id is None:
            continue
        bind.execute(
            sa.text('INSERT INTO case_asset_flag (asset_id, flag_id, case_id, reason, decision_id, set_at, '
                    'set_by_id) VALUES (:asset_id, :flag_id, :case_id, :reason, :decision_id, '
                    'COALESCE(:set_at, now()), :set_by_id) ON CONFLICT DO NOTHING'),
            {'asset_id': row.asset_id, 'flag_id': flag_id, 'case_id': row.case_id, 'reason': row.stage_reason,
             'decision_id': row.stage_decision_id, 'set_at': row.stage_updated_at,
             'set_by_id': row.stage_updated_by_id}
        )


def _insert_history(bind, row, flag_id, flag_name, action, reason, decision_id):
    bind.execute(
        sa.text('INSERT INTO case_asset_flag_history (asset_id, case_id, flag_id, flag_name, action, reason, '
                'decision_id, war_room_id, changed_by_id, changed_at) VALUES (:asset_id, :case_id, :flag_id, '
                ':flag_name, :action, :reason, :decision_id, :war_room_id, :changed_by_id, :changed_at)'),
        {'asset_id': row.asset_id, 'case_id': row.case_id, 'flag_id': flag_id, 'flag_name': flag_name,
         'action': action, 'reason': reason, 'decision_id': decision_id, 'war_room_id': row.war_room_id,
         'changed_by_id': row.changed_by_id, 'changed_at': row.changed_at}
    )


def _copy_stage_history(bind, mapping):
    """A transition A -> B becomes "cleared A" then "set B"; the reason
    and decision go with the flag that was set."""
    if not _has_table('case_asset_stage_history'):
        return
    rows = bind.execute(sa.text(
        'SELECT asset_id, case_id, from_stage_id, from_stage_name, to_stage_id, to_stage_name, reason, '
        'decision_id, war_room_id, changed_by_id, changed_at FROM case_asset_stage_history '
        'ORDER BY changed_at, id')).fetchall()
    for row in rows:
        if row.from_stage_name is not None:
            _insert_history(bind, row, mapping.get(row.from_stage_id), row.from_stage_name, 'cleared',
                            None if row.to_stage_name is not None else row.reason, None)
        if row.to_stage_name is not None:
            _insert_history(bind, row, mapping.get(row.to_stage_id), row.to_stage_name, 'set', row.reason,
                            row.decision_id)


def _drop_stages():
    for column in _STAGE_COLUMNS:
        if _table_has_column('case_assets', column):
            # Postgres drops the foreign keys and indexes of the column with it.
            op.drop_column('case_assets', column)
    if _has_table('case_asset_stage_history'):
        op.drop_table('case_asset_stage_history')
    if _has_table('asset_stage'):
        op.drop_table('asset_stage')


def _convert_stages(bind):
    if not _has_table('asset_stage'):
        return
    flags_empty = not bind.execute(sa.text('SELECT 1 FROM asset_flag LIMIT 1')).first()
    referenced = _stages_are_referenced(bind)
    if flags_empty and not referenced and _stages_are_seeded_default(bind):
        return
    mapping = _copy_stages(bind)
    _copy_current_stages(bind, mapping)
    _copy_stage_history(bind, mapping)


def _seed_default_flags(bind):
    if bind.execute(sa.text('SELECT 1 FROM asset_flag LIMIT 1')).first():
        return
    for position, (name, description, color, icon, kind, requires_reason) in enumerate(_DEFAULT_FLAGS):
        bind.execute(
            sa.text('INSERT INTO asset_flag (name, description, color, icon, kind, sort_order, requires_reason, '
                    'requires_decision) VALUES (:name, :description, :color, :icon, :kind, :sort_order, '
                    ':requires_reason, false)'),
            {'name': name, 'description': description, 'color': color, 'icon': icon, 'kind': kind,
             'sort_order': position, 'requires_reason': requires_reason}
        )


def _rename_hook(bind):
    if not _has_table('iris_hooks'):
        return
    old_id = bind.execute(sa.text('SELECT id FROM iris_hooks WHERE hook_name = :name'), {'name': _OLD_HOOK}).scalar()
    if old_id is None:
        return
    new_id = bind.execute(sa.text('SELECT id FROM iris_hooks WHERE hook_name = :name'), {'name': _NEW_HOOK}).scalar()
    if new_id is None:
        bind.execute(sa.text('UPDATE iris_hooks SET hook_name = :name, hook_description = :description '
                             'WHERE id = :id'),
                     {'name': _NEW_HOOK, 'description': _NEW_HOOK_DESCRIPTION, 'id': old_id})
        return
    if _has_table('iris_module_hooks'):
        bind.execute(sa.text('UPDATE iris_module_hooks SET hook_id = :new_id WHERE hook_id = :old_id'),
                     {'new_id': new_id, 'old_id': old_id})
    bind.execute(sa.text('DELETE FROM iris_hooks WHERE id = :id'), {'id': old_id})


def upgrade():
    bind = op.get_bind()
    _create_flag_tables()
    _convert_stages(bind)
    _drop_stages()
    _seed_default_flags(bind)
    _rename_hook(bind)


def downgrade():
    """Lossy: the flags are dropped and the stage schema comes back empty."""
    bind = op.get_bind()
    if _has_table('iris_hooks'):
        bind.execute(sa.text('UPDATE iris_hooks SET hook_name = :old WHERE hook_name = :new'),
                     {'old': _OLD_HOOK, 'new': _NEW_HOOK})
    for table in ('case_asset_flag_history', 'case_asset_flag', 'asset_flag'):
        if _has_table(table):
            op.drop_table(table)

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
            sa.Column('is_optional', sa.Boolean(), nullable=False, server_default=sa.text('false')),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.CheckConstraint("kind IN ('progress', 'done', 'exception')", name='ck_asset_stage_kind'),
        )
    columns = (
        sa.Column('stage_id', sa.Integer(), sa.ForeignKey('asset_stage.id'), nullable=True),
        sa.Column('stage_reason', sa.Text(), nullable=True),
        sa.Column('stage_decision_id', sa.BigInteger(),
                  sa.ForeignKey('war_room_decision.decision_id', ondelete='SET NULL'), nullable=True),
        sa.Column('stage_updated_at', sa.DateTime(), nullable=True),
        sa.Column('stage_updated_by_id', sa.BigInteger(), sa.ForeignKey('user.id', ondelete='SET NULL'),
                  nullable=True),
    )
    for column in columns:
        if not _table_has_column('case_assets', column.name):
            op.add_column('case_assets', column)
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
