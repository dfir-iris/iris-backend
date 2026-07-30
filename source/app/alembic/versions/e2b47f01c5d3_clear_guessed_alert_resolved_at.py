"""Clear the guessed alert.resolved_at values from the initial backfill

Revision ID: e2b47f01c5d3
Revises: d1a6b3c7e908
Create Date: 2026-07-30 12:00:00.000000

The initial backfill (d1a6b3c7e908) populated `alerts.resolved_at` with
`max(modification_history keys)` for every alert whose
`alert_resolution_status_id` was set. That was a well-meaning heuristic
but it silently conflates two events: (a) the resolution being applied
and (b) any subsequent edit (owner change, tag added, moved between
cases, ...). For alerts resolved months ago but touched recently, the
backfill effectively claims "resolved yesterday", which inflates MTTR
by orders of magnitude.

Corrective policy: null out every backfilled value. Real `resolved_at`
timestamps going forward come from `alerts_update` at the moment an
analyst first sets a resolution — those are ground truth. Legacy
resolved alerts simply won't contribute to MTTR anymore. That's more
honest than a wrong guess: we DON'T know when they were resolved, so
we drop them from the mean rather than distort it.

Heuristic to detect "backfilled" vs "genuine": the first-run code path
that sets resolved_at "for real" runs inside a Python `datetime.utcnow()`
during alerts_update. The backfill code stamped a timestamp derived from
`modification_history` keys via Postgres `to_timestamp(:ra)`. There's no
column-level flag distinguishing them, so we clear everything and let
future updates re-populate for freshly-resolved alerts.

Rows that were resolved AFTER d1a6b3c7e908 ran on this DB (i.e. after
the alerts_update code was deployed) will have accurate values already
— but those are indistinguishable from backfilled ones at the SQL
layer. Better a clean slate than a bimodal population. If an install
needs to retain them, downgrade this migration and they come back
(minus any subsequently-changed rows, which is unavoidable).
"""
from alembic import op
import sqlalchemy as sa

from app.alembic.alembic_utils import _table_has_column


revision = 'e2b47f01c5d3'
down_revision = 'd1a6b3c7e908'
branch_labels = None
depends_on = None


def upgrade():
    if not _table_has_column('alerts', 'resolved_at'):
        return
    op.execute(sa.text('UPDATE alerts SET resolved_at = NULL'))


def downgrade():
    # No-op: we can't reconstruct the backfilled values from history
    # without re-running the same guess. Downgrading this migration
    # simply leaves the column NULL — the DB is in the same state the
    # upgrade left it, minus any subsequently-recorded genuine
    # resolutions. This is intentional: the whole point of the
    # migration is that the backfilled values were wrong.
    pass
