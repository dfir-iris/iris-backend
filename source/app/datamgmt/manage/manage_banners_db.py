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

"""Query helpers for the `banner` table.

Deliberately Flask-free / request-free — every helper is safe to call
from Celery tasks, migrations, or the shell too.
"""

import datetime

from sqlalchemy import or_

from app.models.banners import Banner


def list_banners():
    """All banners, admin-facing order.

    Sort: soonest-starting first (NULLs — "start immediately" — bubble
    up because they're effectively already-started), tiebreak on newest
    id last (largest id = most recently created).
    """
    return (
        Banner.query
        .order_by(Banner.start_at.asc().nulls_first(), Banner.id.desc())
        .all()
    )


def list_active_banners(now: datetime.datetime | None = None):
    """Banners currently within their timespan, oldest-active-first.

    A NULL bound means "no bound on that side". The `end_at > now`
    comparison is strict so a banner expires exactly at `end_at` rather
    than lingering for one more second.
    """
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    return (
        Banner.query
        .filter(or_(Banner.start_at.is_(None), Banner.start_at <= now))
        .filter(or_(Banner.end_at.is_(None), Banner.end_at > now))
        .order_by(Banner.start_at.asc().nulls_first(), Banner.id.asc())
        .all()
    )


def get_banner(banner_id: int):
    return Banner.query.filter(Banner.id == banner_id).first()
