#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Investigation graph.

- `case_graph_relation_type`: catalogue of edge relations, the system
  rows seeded (missing names only, so a re-run never duplicates).
- `case_graph_node`: free nodes of a case.
- `case_graph_edge` / `case_graph_edge_event`: stored edges and their
  supporting timeline events.
- `case_graph_dismissed_suggestion`: dismissed graph suggestions.
- `case_graph_view`: saved views of the graph.
- `role` (`source` | `target` | `involved`) on `case_events_assets` and
  `case_events_ioc`.
- Drops the unused v1 tables `case_graph_links` and `case_graph_assets`.

Idempotent: `db.create_all()` runs before the migrations on a fresh
install and already creates the tables and columns.

Revision ID: a3f6c1d8e2b4
Revises: e7c3a9f2d4b6
Create Date: 2026-10-30
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.alembic.alembic_utils import _has_table
from app.alembic.alembic_utils import _table_has_column


revision = 'a3f6c1d8e2b4'
down_revision = 'e7c3a9f2d4b6'
branch_labels = None
depends_on = None


_RELATION_TYPES = (
    ('related_to', 'Related to', False, 'slate', 'dashed', 'related-to'),
    ('co_occurs', 'Seen together', False, 'slate', 'dashed', 'related-to'),
    ('connected_to', 'Connected to', True, 'sky', 'solid', 'communicates-with'),
    ('authenticated_to', 'Authenticated to', True, 'indigo', 'solid', 'related-to'),
    ('lateral_movement', 'Lateral movement', True, 'red', 'solid', 'related-to'),
    ('executed_on', 'Executed on', True, 'orange', 'solid', 'related-to'),
    ('dropped', 'Dropped', True, 'amber', 'solid', 'drops'),
    ('downloaded_from', 'Downloaded from', True, 'amber', 'solid', 'downloads'),
    ('resolves_to', 'Resolves to', True, 'teal', 'solid', 'resolves-to'),
    ('beacons_to', 'Beacons to', True, 'rose', 'solid', 'beacons-to'),
    ('exfiltrated_to', 'Exfiltrated to', True, 'fuchsia', 'solid', 'exfiltrates-to'),
    ('owns_account', 'Owns account', True, 'violet', 'solid', 'related-to'),
    ('same_as', 'Same as', False, 'emerald', 'dotted', 'duplicate-of'),
    ('parent_of', 'Parent of', True, 'gray', 'dotted', 'related-to'),
    ('involved_in', 'Involved in', True, 'slate', 'dotted', 'related-to'),
)


def _user_fk(name):
    return sa.Column(name, sa.BigInteger(), sa.ForeignKey('user.id', ondelete='SET NULL'), nullable=True)


def _case_fk(primary_key=False):
    return sa.Column('case_id', sa.BigInteger(), sa.ForeignKey('cases.case_id', ondelete='CASCADE'),
                     nullable=False, primary_key=primary_key)


def _tlp_fk():
    return sa.Column('tlp_id', sa.Integer(), sa.ForeignKey('tlp.tlp_id', ondelete='SET NULL'), nullable=True)


def _timestamps():
    return [
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
    ]


def _create_relation_types():
    if not _has_table('case_graph_relation_type'):
        op.create_table(
            'case_graph_relation_type',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('name', sa.String(64), nullable=False, unique=True),
            sa.Column('label', sa.Text(), nullable=False),
            sa.Column('description', sa.Text(), nullable=True),
            sa.Column('directed', sa.Boolean(), nullable=False, server_default=sa.text('true')),
            sa.Column('color', sa.String(16), nullable=False, server_default=sa.text("'slate'")),
            sa.Column('line_style', sa.String(16), nullable=False, server_default=sa.text("'solid'")),
            sa.Column('stix_relationship', sa.String(64), nullable=True),
            sa.Column('is_system', sa.Boolean(), nullable=False, server_default=sa.text('false')),
            sa.Column('sort_order', sa.Integer(), nullable=False, server_default=sa.text('0')),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
            sa.CheckConstraint("line_style IN ('solid', 'dashed', 'dotted')",
                               name='ck_case_graph_relation_type_line_style'),
        )
    bind = op.get_bind()
    for position, (name, label, directed, color, line_style, stix) in enumerate(_RELATION_TYPES):
        bind.execute(
            sa.text('INSERT INTO case_graph_relation_type '
                    '(name, label, directed, color, line_style, stix_relationship, is_system, sort_order) '
                    'VALUES (:name, :label, :directed, :color, :line_style, :stix, true, :sort_order) '
                    'ON CONFLICT (name) DO NOTHING'),
            {'name': name, 'label': label, 'directed': directed, 'color': color,
             'line_style': line_style, 'stix': stix, 'sort_order': position},
        )


def _create_nodes():
    if _has_table('case_graph_node'):
        return
    op.create_table(
        'case_graph_node',
        sa.Column('node_id', sa.BigInteger(), primary_key=True),
        sa.Column('node_uuid', postgresql.UUID(as_uuid=True), nullable=False,
                  server_default=sa.text('gen_random_uuid()')),
        _case_fk(),
        sa.Column('label', sa.Text(), nullable=False),
        sa.Column('node_type', sa.String(32), nullable=False, server_default=sa.text("'other'")),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('icon', sa.String(64), nullable=True),
        sa.Column('color', sa.String(16), nullable=True),
        _tlp_fk(),
        sa.Column('custom_attributes', postgresql.JSONB(), nullable=True),
        _user_fk('created_by_id'),
        *_timestamps(),
    )
    op.create_index('ix_case_graph_node_case_id', 'case_graph_node', ['case_id'])


def _create_edges():
    if not _has_table('case_graph_edge'):
        op.create_table(
            'case_graph_edge',
            sa.Column('edge_id', sa.BigInteger(), primary_key=True),
            sa.Column('edge_uuid', postgresql.UUID(as_uuid=True), nullable=False,
                      server_default=sa.text('gen_random_uuid()')),
            _case_fk(),
            sa.Column('source_kind', sa.String(16), nullable=False),
            sa.Column('source_id', sa.BigInteger(), nullable=False),
            sa.Column('target_kind', sa.String(16), nullable=False),
            sa.Column('target_id', sa.BigInteger(), nullable=False),
            sa.Column('relation_type_id', sa.Integer(), sa.ForeignKey('case_graph_relation_type.id'),
                      nullable=False),
            sa.Column('directed', sa.Boolean(), nullable=False),
            sa.Column('label', sa.Text(), nullable=True),
            sa.Column('description', sa.Text(), nullable=True),
            sa.Column('confidence', sa.SmallInteger(), nullable=True),
            sa.Column('first_seen', sa.DateTime(), nullable=True),
            sa.Column('last_seen', sa.DateTime(), nullable=True),
            _tlp_fk(),
            sa.Column('origin', sa.String(16), nullable=False, server_default=sa.text("'manual'")),
            sa.Column('review_state', sa.String(16), nullable=False, server_default=sa.text("'accepted'")),
            sa.Column('custom_attributes', postgresql.JSONB(), nullable=True),
            _user_fk('created_by_id'),
            *_timestamps(),
            sa.CheckConstraint('NOT (source_kind = target_kind AND source_id = target_id)',
                               name='ck_case_graph_edge_no_loop'),
            sa.CheckConstraint('confidence IS NULL OR (confidence >= 0 AND confidence <= 100)',
                               name='ck_case_graph_edge_confidence'),
        )
        op.create_index('ix_case_graph_edge_case_id', 'case_graph_edge', ['case_id'])
        op.create_index('idx_case_graph_edge_source', 'case_graph_edge', ['case_id', 'source_kind', 'source_id'])
        op.create_index('idx_case_graph_edge_target', 'case_graph_edge', ['case_id', 'target_kind', 'target_id'])

    if not _has_table('case_graph_edge_event'):
        op.create_table(
            'case_graph_edge_event',
            sa.Column('edge_id', sa.BigInteger(), sa.ForeignKey('case_graph_edge.edge_id', ondelete='CASCADE'),
                      primary_key=True, nullable=False),
            sa.Column('event_id', sa.BigInteger(), sa.ForeignKey('cases_events.event_id', ondelete='CASCADE'),
                      primary_key=True, nullable=False),
        )
        op.create_index('ix_case_graph_edge_event_event_id', 'case_graph_edge_event', ['event_id'])


def _create_dismissed_suggestions():
    if _has_table('case_graph_dismissed_suggestion'):
        return
    op.create_table(
        'case_graph_dismissed_suggestion',
        _case_fk(primary_key=True),
        sa.Column('suggestion_key', sa.String(255), primary_key=True, nullable=False),
        _user_fk('dismissed_by_id'),
        sa.Column('dismissed_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
    )


def _create_views():
    if _has_table('case_graph_view'):
        return
    op.create_table(
        'case_graph_view',
        sa.Column('view_id', sa.BigInteger(), primary_key=True),
        sa.Column('view_uuid', postgresql.UUID(as_uuid=True), nullable=False,
                  server_default=sa.text('gen_random_uuid()')),
        _case_fk(),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        _user_fk('owner_id'),
        sa.Column('is_shared', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('is_report_default', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('state', postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column('snapshot', sa.Text(), nullable=True),
        *_timestamps(),
    )
    op.create_index('ix_case_graph_view_case_id', 'case_graph_view', ['case_id'])


def _add_roles():
    for table in ('case_events_assets', 'case_events_ioc'):
        if not _table_has_column(table, 'role'):
            op.add_column(table, sa.Column('role', sa.String(16), nullable=False,
                                           server_default=sa.text("'involved'")))


def _drop_legacy_tables():
    if _has_table('case_graph_links'):
        op.drop_table('case_graph_links')
    if _has_table('case_graph_assets'):
        op.drop_table('case_graph_assets')


def _recreate_legacy_tables():
    if not _has_table('case_graph_assets'):
        op.create_table(
            'case_graph_assets',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('case_id', sa.BigInteger(), sa.ForeignKey('cases.case_id'), nullable=True),
            sa.Column('asset_id', sa.Integer(), nullable=True),
            sa.Column('asset_type_id', sa.Integer(), sa.ForeignKey('assets_type.asset_id'), nullable=True),
        )
    if not _has_table('case_graph_links'):
        op.create_table(
            'case_graph_links',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('case_id', sa.BigInteger(), sa.ForeignKey('cases.case_id'), nullable=True),
            sa.Column('source_id', sa.Integer(), sa.ForeignKey('case_graph_assets.id'), nullable=True),
            sa.Column('dest_id', sa.Integer(), sa.ForeignKey('case_graph_assets.id'), nullable=True),
        )


def upgrade():
    _create_relation_types()
    _create_nodes()
    _create_edges()
    _create_dismissed_suggestions()
    _create_views()
    _add_roles()
    _drop_legacy_tables()


def downgrade():
    _recreate_legacy_tables()
    for table in ('case_events_assets', 'case_events_ioc'):
        if _table_has_column(table, 'role'):
            op.drop_column(table, 'role')
    for table in ('case_graph_view', 'case_graph_dismissed_suggestion', 'case_graph_edge_event',
                  'case_graph_edge', 'case_graph_node', 'case_graph_relation_type'):
        if _has_table(table):
            op.drop_table(table)
