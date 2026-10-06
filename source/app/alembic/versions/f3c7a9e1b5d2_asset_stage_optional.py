"""Asset stages — optional progress stages, leaner default taxonomy.

- `asset_stage.is_optional`: a progress stage an asset may skip on its
  way to done (e.g. Identified -> Patched without being Isolated).
- Default taxonomy tuning: "Analysed" duplicated the asset analysis
  status, and "Isolated" does not apply to every asset. Only when the
  taxonomy is still exactly the untouched default seeded by
  e8b1c4d2a7f9 and no asset (current or past) ever used "Analysed",
  that stage is removed and "Isolated" is flagged optional. Any
  customised taxonomy is left alone. The guard no longer matches once
  applied, so re-running is a no-op.

Idempotent via `_table_has_column`: `db.create_all()` already creates
the column on a fresh install.

Revision ID: f3c7a9e1b5d2
Revises: e8b1c4d2a7f9
Create Date: 2026-10-06 16:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

from app.alembic.alembic_utils import _has_table
from app.alembic.alembic_utils import _table_has_column


revision = 'f3c7a9e1b5d2'
down_revision = 'e8b1c4d2a7f9'
branch_labels = None
depends_on = None


# (name, kind, sort_order) of the taxonomy seeded by e8b1c4d2a7f9
_SEEDED_DEFAULT = {
    ('Identified', 'progress', 0),
    ('Isolated', 'progress', 1),
    ('Analysed', 'progress', 2),
    ('Patched', 'progress', 3),
    ('Restored', 'done', 4),
    ('Unpatched', 'exception', 5),
    ('Blocked', 'exception', 6),
}


def _is_untouched_default(bind):
    rows = bind.execute(sa.text('SELECT name, kind, sort_order FROM asset_stage')).fetchall()
    return {(r[0], r[1], r[2]) for r in rows} == _SEEDED_DEFAULT and len(rows) == len(_SEEDED_DEFAULT)


def _stage_is_referenced(bind, stage_id):
    in_use = bind.execute(
        sa.text('SELECT 1 FROM case_assets WHERE stage_id = :id LIMIT 1'), {'id': stage_id}
    ).first()
    if in_use:
        return True
    if not _has_table('case_asset_stage_history'):
        return False
    in_history = bind.execute(
        sa.text('SELECT 1 FROM case_asset_stage_history '
                'WHERE from_stage_id = :id OR to_stage_id = :id LIMIT 1'),
        {'id': stage_id}
    ).first()
    return in_history is not None


def _tune_default_taxonomy():
    bind = op.get_bind()
    if not _is_untouched_default(bind):
        return

    analysed_id = bind.execute(sa.text("SELECT id FROM asset_stage WHERE name = 'Analysed'")).scalar()
    if _stage_is_referenced(bind, analysed_id):
        return

    bind.execute(sa.text('DELETE FROM asset_stage WHERE id = :id'), {'id': analysed_id})
    bind.execute(sa.text("UPDATE asset_stage SET is_optional = true WHERE name = 'Isolated'"))
    bind.execute(sa.text('UPDATE asset_stage SET sort_order = sort_order - 1 WHERE sort_order > 2'))


def upgrade():
    if not _has_table('asset_stage'):
        return

    if not _table_has_column('asset_stage', 'is_optional'):
        op.add_column('asset_stage', sa.Column('is_optional', sa.Boolean(), nullable=False,
                                               server_default=sa.text('false')))

    _tune_default_taxonomy()


def downgrade():
    if _table_has_column('asset_stage', 'is_optional'):
        op.drop_column('asset_stage', 'is_optional')
