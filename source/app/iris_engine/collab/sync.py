#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 3 of the License, or (at your option) any later version.

"""Server-side writes INTO an already-seeded collaborative document.

Background. `business/collab.py` makes the Y.Doc authoritative the
moment a document is first opened: `ensure_snapshot` creates the
`collab_doc` row from the source column once, and from then on it
returns the stored `y_state` and *ignores* `current_content`. The
mirror image is `flush_to_source`, which renders the Y.Doc back over
the source column on last-client-disconnect.

Consequence for any backend feature that appends to a markdown column
(alert escalation writing a mention chip into `cases.description`,
say): the append is invisible in the editor and is erased by the next
flush, because the Y.Doc never learned about it. Opening a case once
is enough to create the row — iris-frontend mounts the summary editor
on every case page visit, read-only included.

So a server-side append has to land in the Y.Doc too. That's what
`collab_append_markdown` is for. It is the DB-aware counterpart to the
pure `render.append_markdown_to_ydoc_update` primitive, kept here
rather than in `business/collab.py` because the callers live in
`app.datamgmt` and `datamgmt → business` is forbidden by the layering
(see CLAUDE.md and the import-linter contracts in `pyproject.toml`).

The source column stays the caller's responsibility. Both writes are
needed and neither is redundant:
  * the column serves REST readers, search and exports, and is the
    ONLY thing that works before the document is ever opened;
  * the Y.Doc serves the editor, and wins on the next flush.
"""

from __future__ import annotations

import datetime
import logging

from app.db import db
from app.iris_engine.collab.render import append_markdown_to_ydoc_update
from app.iris_engine.collab.render import ydoc_update_to_markdown
from app.models.collab import CollabDoc


logger = logging.getLogger(__name__)


def collab_append_markdown(doc_name: str, md: str) -> None:
    """Append `md` to the end of the Y.Doc behind `doc_name`.

    No-op when no `collab_doc` row exists — that document has never
    been opened, so the source column is still authoritative and
    `ensure_snapshot` will seed the Y.Doc from it (append included) on
    first open. Creating a row here would only move the seeding forward
    in time and risk freezing a stale column into the CRDT.

    NEVER raises. Callers are escalation / merge flows whose real work
    is already committed by the time they get here; failing them
    because a CRDT append went wrong would be strictly worse than the
    chip being missing. Same defensive posture as `flush_to_source`,
    which swallows render errors for the same reason.

    Known limitation — clients currently in the room do NOT see this
    append live. `app/__init__.py` builds `SocketIO` with no
    `message_queue`, so a REST request or Celery task handled by one
    gunicorn worker cannot emit into a collab room held by another.
    Nothing is lost: the authoritative `y_state` has the append, and
    Yjs merges are commutative, so an editor that reconnects (or any
    later joiner) converges on a document containing both this append
    and whatever was typed meanwhile. It just isn't real-time.
    """
    try:
        row = CollabDoc.query.filter_by(doc_name=doc_name).first()
        if row is None:
            return

        new_state = append_markdown_to_ydoc_update(
            bytes(row.y_state) if row.y_state else None, md,
        )
        row.y_state = new_state
        # Keep the cached markdown coherent with the state we just
        # wrote, the way `flush_to_source` does — a REST reader
        # consuming `content_md` must not see a doc that predates the
        # append.
        row.content_md = ydoc_update_to_markdown(new_state)
        row.last_flushed_at = datetime.datetime.utcnow()
        # `updated_by_id` is deliberately left alone: it is the audit
        # trail's "last human who edited", and `flush_to_source` hands
        # it to `track_activity`. Attributing a machine-written chip
        # to whoever last typed in the document would be a lie.
        db.session.commit()
    except Exception:
        logger.exception('collab sync: failed to append to %r', doc_name)
        try:
            db.session.rollback()
        except Exception:
            logger.exception('collab sync: rollback failed for %r', doc_name)
