"""Deleting an investigation flow detaches it from alerts and clusters.

`alerts.alert_investigation_flow_id` and
`alert_clusters.cluster_investigation_flow_id` referenced
`investigation_flows` without an ON DELETE rule, so a flow that had ever
been attached to an alert or a cluster could not be deleted (foreign key
violation, HTTP 500). Both become ON DELETE SET NULL; the checklist
progress already goes with the steps (ON DELETE CASCADE).

The existing constraint is looked up by column rather than by name: it
was created inline, by `db.create_all()` on fresh installs and by
a1c9f7b2e3d4 / b2d0e8a9f4c1 on upgraded ones.

Revision ID: e5b9c2a7d1f3
Revises: d4a8f1c6b2e9
Create Date: 2026-10-08 12:30:00.000000
"""
import sqlalchemy as sa
from alembic import op

from app.alembic.alembic_utils import _table_has_column


revision = 'e5b9c2a7d1f3'
down_revision = 'd4a8f1c6b2e9'
branch_labels = None
depends_on = None


_FLOW_REFERENCES = (
    ('alerts', 'alert_investigation_flow_id'),
    ('alert_clusters', 'cluster_investigation_flow_id'),
)


def _flow_fk_names(table, column):
    bind = op.get_bind()
    return [
        fk['name'] for fk in sa.inspect(bind).get_foreign_keys(table)
        if fk.get('referred_table') == 'investigation_flows'
        and fk.get('constrained_columns') == [column]
        and fk.get('name')
    ]


def _replace_flow_fk(table, column, ondelete):
    if not _table_has_column(table, column):
        return
    for name in _flow_fk_names(table, column):
        op.drop_constraint(name, table, type_='foreignkey')
    op.create_foreign_key(f'{table}_{column}_fkey', table, 'investigation_flows',
                          [column], ['flow_id'], ondelete=ondelete)


def upgrade():
    for table, column in _FLOW_REFERENCES:
        _replace_flow_fk(table, column, 'SET NULL')


def downgrade():
    for table, column in _FLOW_REFERENCES:
        _replace_flow_fk(table, column, None)
