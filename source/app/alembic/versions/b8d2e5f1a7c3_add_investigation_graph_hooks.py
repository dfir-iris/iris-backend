#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Module hooks of the investigation graph.

Seeds `on_postload_graph_edge_create`, `on_postload_graph_edge_update`,
`on_postload_graph_edge_delete` and `on_postload_graph_node_create` in
`iris_hooks` (missing names only: `create_safe_hooks()` seeds them too at
boot, after the migrations).

Revision ID: b8d2e5f1a7c3
Revises: a3f6c1d8e2b4
Create Date: 2026-10-30
"""
import sqlalchemy as sa
from alembic import op

from app.alembic.alembic_utils import _has_table


revision = 'b8d2e5f1a7c3'
down_revision = 'a3f6c1d8e2b4'
branch_labels = None
depends_on = None

_HOOKS = (
    ('on_postload_graph_edge_create', 'Triggered on investigation graph edge creation, after commit in DB'),
    ('on_postload_graph_edge_update', 'Triggered on investigation graph edge update, after commit in DB'),
    ('on_postload_graph_edge_delete', 'Triggered on investigation graph edge deletion, after commit in DB'),
    ('on_postload_graph_node_create', 'Triggered on investigation graph free node creation, after commit in DB'),
)


def upgrade():
    if not _has_table('iris_hooks'):
        return
    bind = op.get_bind()
    for name, description in _HOOKS:
        exists = bind.execute(sa.text('SELECT id FROM iris_hooks WHERE hook_name = :name'), {'name': name}).scalar()
        if exists is None:
            bind.execute(sa.text('INSERT INTO iris_hooks (hook_name, hook_description) VALUES (:name, :description)'),
                         {'name': name, 'description': description})


def downgrade():
    if not _has_table('iris_hooks'):
        return
    bind = op.get_bind()
    names = [name for name, _ in _HOOKS]
    if _has_table('iris_module_hooks'):
        bind.execute(sa.text('DELETE FROM iris_module_hooks WHERE hook_id IN '
                             '(SELECT id FROM iris_hooks WHERE hook_name = ANY(:names))'), {'names': names})
    bind.execute(sa.text('DELETE FROM iris_hooks WHERE hook_name = ANY(:names)'), {'names': names})
