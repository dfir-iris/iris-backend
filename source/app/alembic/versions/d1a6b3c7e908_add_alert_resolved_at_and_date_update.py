"""Add resolved_at + date_update to alerts, backfill from modification_history

Revision ID: d1a6b3c7e908
Revises: c9e4a12b7f83
Create Date: 2026-07-30 10:00:00.000000

Motivation: MTTR-for-alerts needs a first-class timestamp for when an
analyst set a resolution. Walking `modification_history` JSON in
aggregates is fragile and slow. Two new nullable timestamp columns:

  * alert_date_update -- last write, mirrors the pattern on cases/notes.
  * alert_resolved_at -- when alert_resolution_status_id first went from
                         null to non-null.

Backfill: for every existing alert we scan `modification_history` (a
JSON dict keyed on Unix-timestamp strings) and derive:
  * date_update  = max(key) -> to_timestamp
  * resolved_at  = max(key) if alert_resolution_status_id is set, else
                   null. Best approximation given history has no
                   per-event before/after diff — most alerts get one
                   substantive edit (the resolution), so max-key is a
                   reasonable proxy.

Rows with an empty / missing history dict but a resolution status set
fall back to alert_creation_time (better than null: "we know it was
resolved at some point, we just don't know when; count it as instant"
so it doesn't distort the MTTR mean upward).

Idempotent — column adds are guarded on presence.
"""
import json
from alembic import op
import sqlalchemy as sa

from app.alembic.alembic_utils import _table_has_column


revision = 'd1a6b3c7e908'
down_revision = 'c9e4a12b7f83'
branch_labels = None
depends_on = None


def upgrade():
    if not _table_has_column('alerts', 'date_update'):
        op.add_column('alerts', sa.Column('date_update', sa.DateTime, nullable=True))
    if not _table_has_column('alerts', 'resolved_at'):
        op.add_column('alerts', sa.Column('resolved_at', sa.DateTime, nullable=True))

    op.create_index(
        'ix_alerts_resolved_at',
        'alerts',
        ['resolved_at'],
        postgresql_where=sa.text('resolved_at IS NOT NULL'),
        if_not_exists=True,
    )

    bind = op.get_bind()

    # Backfill in a single pass over the table. modification_history is a
    # JSON *object* keyed on stringified Unix floats. We pull the raw
    # column, decode, then take max(keys). Chunking not needed at typical
    # scale (< a few M alerts) — this runs inside the migration
    # transaction.
    rows = bind.execute(
        sa.text(
            'SELECT alert_id, alert_creation_time, alert_resolution_status_id, '
            'modification_history FROM alerts'
        )
    ).fetchall()

    for alert_id, creation_time, resolution_status_id, history in rows:
        max_ts = None
        if isinstance(history, dict) and history:
            max_key = None
            for k in history.keys():
                try:
                    v = float(k)
                except (TypeError, ValueError):
                    continue
                if max_key is None or v > max_key:
                    max_key = v
            if max_key is not None:
                max_ts = max_key
        elif isinstance(history, str):
            # Some rows may have been serialized as a JSON string.
            try:
                parsed = json.loads(history)
                if isinstance(parsed, dict) and parsed:
                    max_key = None
                    for k in parsed.keys():
                        try:
                            v = float(k)
                        except (TypeError, ValueError):
                            continue
                        if max_key is None or v > max_key:
                            max_key = v
                    if max_key is not None:
                        max_ts = max_key
            except (TypeError, ValueError):
                pass

        # date_update: last touch, best guess from history.
        # resolved_at: same, but only if resolution set — else null.
        # Fallback for resolved-but-no-history rows: creation time.
        date_update_val = max_ts
        resolved_at_val = None
        if resolution_status_id is not None:
            resolved_at_val = max_ts if max_ts is not None else (
                creation_time.timestamp() if creation_time is not None else None
            )

        bind.execute(
            sa.text(
                'UPDATE alerts SET '
                'date_update = CASE WHEN :du IS NULL THEN NULL ELSE to_timestamp(:du) END, '
                'resolved_at = CASE WHEN :ra IS NULL THEN NULL ELSE to_timestamp(:ra) END '
                'WHERE alert_id = :aid'
            ),
            {'du': date_update_val, 'ra': resolved_at_val, 'aid': alert_id},
        )


def downgrade():
    op.drop_index('ix_alerts_resolved_at', table_name='alerts', if_exists=True)
    if _table_has_column('alerts', 'resolved_at'):
        op.drop_column('alerts', 'resolved_at')
    if _table_has_column('alerts', 'date_update'):
        op.drop_column('alerts', 'date_update')
