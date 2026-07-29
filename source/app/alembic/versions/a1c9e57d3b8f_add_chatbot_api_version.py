"""Placeholder for the withdrawn chatbot_api_version column.

An earlier iteration of the chatbot work added a dedicated Azure
OpenAI provider with an `api_version` query parameter — which required
this column on `server_settings`. That approach was abandoned in
favour of running Azure Foundry through the existing Anthropic
provider (which just needs a custom base_url pointing at
`https://<resource>.openai.azure.com/anthropic` — no api-version, no
new provider class, no new column).

Some deployments (dev environments, early adopters) already ran the
original migration before it was withdrawn. The Alembic version table
in those DBs is stamped with this revision id, so simply deleting the
file breaks `alembic upgrade head` with:

    Can't locate revision identified by 'a1c9e57d3b8f'

Keeping this file as a graph placeholder resolves that. `upgrade()`
tolerates both states — column present (rollback) or absent
(never-installed) — and leaves the schema without the column either
way.

Revision ID: a1c9e57d3b8f
Revises: e4a1c73b8f52
Create Date: 2026-07-29 12:00:00.000000
"""
from alembic import op

from app.alembic.alembic_utils import _table_has_column


revision = 'a1c9e57d3b8f'
down_revision = 'e4a1c73b8f52'
branch_labels = None
depends_on = None


def upgrade():
    # Drop the column if a prior run of this migration installed it —
    # otherwise this is a no-op. Never creates it: the follow-up
    # design uses the Anthropic provider's `base_url` for Azure Foundry
    # instead, so `chatbot_api_version` has no consumer in the code.
    if _table_has_column('server_settings', 'chatbot_api_version'):
        op.drop_column('server_settings', 'chatbot_api_version')


def downgrade():
    # Nothing to reverse — this migration is a marker in the graph.
    pass
