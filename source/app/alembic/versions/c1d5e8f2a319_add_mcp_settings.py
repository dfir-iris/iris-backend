"""Add MCP (Model Context Protocol) server toggle and related settings.

Adds five columns to `server_settings` gating the MCP endpoint at
`/api/v2/mcp`, plus a per-user `mcp_allowed` opt-out flag on `user`
so admins can disable MCP for individual high-privilege accounts
without disabling MCP globally.

Idempotent via `_table_has_column` — safe to re-run.

Revision ID: c1d5e8f2a319
Revises: b9c4e7f1a208
Create Date: 2026-07-28 10:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

from app.alembic.alembic_utils import _table_has_column


revision = 'c1d5e8f2a319'
down_revision = 'b9c4e7f1a208'
branch_labels = None
depends_on = None


_MCP_COLUMNS = (
    ('mcp_enabled', sa.Boolean(), sa.text('false'), False),
    ('mcp_max_calls_per_minute_per_worker', sa.Integer(), sa.text('60'), False),
    ('mcp_expose_admin_tools', sa.Boolean(), sa.text('false'), False),
    ('mcp_tool_allowlist', sa.Text(), sa.text("''"), False),
    ('mcp_tool_denylist', sa.Text(), sa.text("''"), False),
)


def upgrade():
    for name, col_type, default, nullable in _MCP_COLUMNS:
        if not _table_has_column('server_settings', name):
            op.add_column(
                'server_settings',
                sa.Column(name, col_type, nullable=nullable,
                          server_default=default),
            )

    if not _table_has_column('user', 'mcp_allowed'):
        op.add_column(
            'user',
            sa.Column('mcp_allowed', sa.Boolean(), nullable=False,
                      server_default=sa.text('true')),
        )


def downgrade():
    if _table_has_column('user', 'mcp_allowed'):
        op.drop_column('user', 'mcp_allowed')

    for name, _type, _default, _nullable in reversed(_MCP_COLUMNS):
        if _table_has_column('server_settings', name):
            op.drop_column('server_settings', name)
