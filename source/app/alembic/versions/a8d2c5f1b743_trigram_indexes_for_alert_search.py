#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Trigram indexes for the alert search bar.

The search expression compiles a field-less term into `ILIKE '%term%'`
OR'd across the six columns in `lucene.alert_fields.DEFAULT_SEARCH_COLUMNS`.
A leading wildcard makes a btree index useless, so without these the
cheapest plan for "crowdstrike" is a sequential scan of `alerts` — and
the bar invites analysts to type one on every keystroke they commit.

All six columns are indexed rather than the obvious two. Postgres can
only turn the OR into a BitmapOr when *every* branch is indexable; leave
one column out and the planner falls back to the sequential scan for the
whole expression, which is the case this migration exists to avoid. The
cost is paid on ingest, where each insert now maintains six GIN indexes.
That trade is deliberate: alerts are written once and searched from then
on.

`pg_trgm` is an extension, and `CREATE EXTENSION` needs a superuser. A
deployment whose application role is not one is a supported deployment,
so a refusal here is logged and the indexes are skipped — the searches
still return the right rows, more slowly. Failing would take the whole
stack down, since migrations run before gunicorn binds.

Idempotent via `index_exists` — safe to re-run.

Revision ID: a8d2c5f1b743
Revises: f4b7c1e0a92d
Create Date: 2026-09-14
"""
from __future__ import annotations

import logging

from alembic import op
from sqlalchemy import text

from app.alembic.alembic_utils import _has_table
from app.alembic.alembic_utils import index_exists


revision = 'a8d2c5f1b743'
down_revision = 'f4b7c1e0a92d'
branch_labels = None
depends_on = None

_log = logging.getLogger('alembic.runtime.migration')

_TABLE = 'alerts'

#: Mirrors `app.datamgmt.lucene.alert_fields.DEFAULT_SEARCH_COLUMNS`, not
#: imported from it: a migration describes the schema as it was on the day
#: it ran, and must keep doing so after that tuple is edited.
_COLUMNS = (
    'alert_title',
    'alert_description',
    'alert_source',
    'alert_source_ref',
    'alert_tags',
    'alert_note',
)


def _index_name(column):
    return f'ix_{_TABLE}_{column}_trgm'


def _enable_pg_trgm():
    """Enable pg_trgm, returning whether it is usable afterwards."""
    bind = op.get_bind()

    if bind.dialect.name != 'postgresql':
        return False

    already_installed = bind.execute(
        text("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'")
    ).scalar()
    if already_installed:
        return True

    try:
        # A savepoint, because a failed statement poisons the whole
        # Postgres transaction: without one, a permission error here
        # would make every later migration in this run fail too.
        with bind.begin_nested():
            bind.execute(text('CREATE EXTENSION IF NOT EXISTS pg_trgm'))
    except Exception as exception:
        _log.warning(
            'Could not enable the pg_trgm extension (%s). Alert search will '
            'work but scan sequentially; run "CREATE EXTENSION pg_trgm;" as a '
            'superuser and re-run the migrations to index it.', exception
        )
        return False

    return True


def upgrade():
    if not _has_table(_TABLE):
        return

    if not _enable_pg_trgm():
        return

    for column in _COLUMNS:
        name = _index_name(column)
        if index_exists(_TABLE, name):
            continue

        op.create_index(
            name, _TABLE, [column],
            postgresql_using='gin',
            postgresql_ops={column: 'gin_trgm_ops'},
        )


def downgrade():
    if not _has_table(_TABLE):
        return

    for column in _COLUMNS:
        name = _index_name(column)
        if index_exists(_TABLE, name):
            op.drop_index(name, table_name=_TABLE)

    # The extension is deliberately left in place: dropping it would take
    # any other trigram index in the database with it.
