#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Indexes for the case timeline search view.

The search view pages a case's events by `(event_date, event_id)`, bins
them by date for its histogram, and hydrates each page with its assets,
IOCs, comments and children. None of those paths was indexed: every one
of them scanned `cases_events` or a link table whole. The btree indexes
below turn them into range scans scoped to one case or one event.

The trigram indexes serve field-less terms, which compile to `ILIKE
'%term%'` OR'd across the columns in
`lucene.event_fields.DEFAULT_EVENT_SEARCH_COLUMNS`. As for the alert
indexes (`a8d2c5f1b743`), every one of those columns is indexed, or the
planner cannot use a BitmapOr and scans the table for the whole
expression. A refused `CREATE EXTENSION pg_trgm` is logged and the
trigram indexes skipped — searches still answer, more slowly.

Idempotent via `index_exists` — safe to re-run.

Revision ID: c4e9a2f7d1b5
Revises: b8d2e5f1a7c3
Create Date: 2026-11-08
"""
from __future__ import annotations

import logging

from alembic import op
from sqlalchemy import text

from app.alembic.alembic_utils import _has_table
from app.alembic.alembic_utils import index_exists


revision = 'c4e9a2f7d1b5'
down_revision = 'b8d2e5f1a7c3'
branch_labels = None
depends_on = None

_log = logging.getLogger('alembic.runtime.migration')

_EVENTS_TABLE = 'cases_events'

#: `(table, index name, columns)`.
_BTREE_INDEXES = (
    (_EVENTS_TABLE, 'ix_cases_events_case_id_event_date', ['case_id', 'event_date', 'event_id']),
    (_EVENTS_TABLE, 'ix_cases_events_parent_event_id', ['parent_event_id']),
    ('case_events_assets', 'ix_case_events_assets_event_id', ['event_id']),
    ('case_events_ioc', 'ix_case_events_ioc_event_id', ['event_id']),
    ('event_comments', 'ix_event_comments_comment_event_id', ['comment_event_id']),
)

#: Mirrors `app.datamgmt.lucene.event_fields.DEFAULT_EVENT_SEARCH_COLUMNS`,
#: not imported from it: a migration describes the schema as it was on the
#: day it ran.
_TRIGRAM_COLUMNS = (
    'event_title',
    'event_content',
    'event_source',
    'event_tags',
    'event_raw',
)


def _trigram_index_name(column):
    return f'ix_{_EVENTS_TABLE}_{column}_trgm'


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
        # A savepoint: a failed statement poisons the whole Postgres
        # transaction, and with it every later migration of this run.
        with bind.begin_nested():
            bind.execute(text('CREATE EXTENSION IF NOT EXISTS pg_trgm'))
    except Exception as exception:
        _log.warning(
            'Could not enable the pg_trgm extension (%s). Timeline search will '
            'work but scan sequentially; run "CREATE EXTENSION pg_trgm;" as a '
            'superuser and re-run the migrations to index it.', exception
        )
        return False

    return True


def upgrade():
    for table, name, columns in _BTREE_INDEXES:
        if not _has_table(table) or index_exists(table, name):
            continue
        op.create_index(name, table, columns)

    if not _has_table(_EVENTS_TABLE):
        return

    if not _enable_pg_trgm():
        return

    for column in _TRIGRAM_COLUMNS:
        name = _trigram_index_name(column)
        if index_exists(_EVENTS_TABLE, name):
            continue

        op.create_index(
            name, _EVENTS_TABLE, [column],
            postgresql_using='gin',
            postgresql_ops={column: 'gin_trgm_ops'},
        )


def downgrade():
    if _has_table(_EVENTS_TABLE):
        for column in _TRIGRAM_COLUMNS:
            name = _trigram_index_name(column)
            if index_exists(_EVENTS_TABLE, name):
                op.drop_index(name, table_name=_EVENTS_TABLE)

    for table, name, _columns in _BTREE_INDEXES:
        if _has_table(table) and index_exists(table, name):
            op.drop_index(name, table_name=table)
