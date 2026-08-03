#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 3 of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
#  Lesser General Public License for more details.
#
#  You should have received a copy of the GNU Lesser General Public License
#  along with this program; if not, write to the Free Software Foundation,
#  Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.

"""Admin-managed top banners.

Server administrators publish a `Banner` row from Settings → Banners; every
authenticated user then sees the row rendered as a colored strip at the top
of the app while its timespan is current. `start_at` / `end_at` are both
optional — a NULL bound means "no lower bound" / "no upper bound", so an
indefinite banner is just `start_at IS NULL AND end_at IS NULL`.

`purpose` is stored as a short string rather than a Postgres enum: adding a
new purpose (e.g. 'success') should only require a marshmallow validator
change, not a migration. A `CHECK` in the migration keeps garbage out today.
"""

from sqlalchemy import BigInteger
from sqlalchemy import Boolean
from sqlalchemy import Column
from sqlalchemy import DateTime
from sqlalchemy import ForeignKey
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy.sql import func

from app.db import db


BANNER_PURPOSES = ('info', 'warning', 'error')


class Banner(db.Model):
    __tablename__ = 'banner'

    id = Column(BigInteger, primary_key=True)
    text = Column(Text, nullable=False)
    purpose = Column(String(16), nullable=False)
    dismissable = Column(Boolean, nullable=False, default=True)

    # NULL = unbounded on that side. A banner with both bounds NULL is
    # always active; a banner with only `end_at` set is active from now
    # until that end.
    start_at = Column(DateTime(timezone=True), nullable=True)
    end_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime, nullable=False, server_default=func.now())
    # `onupdate=func.now()` bumps on every UPDATE — the frontend uses
    # this timestamp as part of the localStorage dismissal key so an
    # admin edit forces a re-display for users who had dismissed the
    # previous revision.
    updated_at = Column(DateTime, nullable=False,
                        server_default=func.now(), onupdate=func.now())
    created_by = Column(ForeignKey('user.id'), nullable=True)
