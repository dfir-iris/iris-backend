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

"""Business layer for admin-managed top banners.

Every write path emits a `track_activity(..., ctx_less=True)` row so the
audit surface shows exactly who published, edited, or removed a banner.
`ctx_less=True` is the same flag used by server-settings changes —
banners are org-wide, not tied to a case.
"""

from app.datamgmt.db_operations import db_create
from app.datamgmt.db_operations import db_delete
from app.datamgmt.manage.manage_banners_db import get_banner
from app.datamgmt.manage.manage_banners_db import list_active_banners
from app.datamgmt.manage.manage_banners_db import list_banners
from app.db import db
from app.iris_engine.utils.tracker import track_activity
from app.models.banners import Banner
from app.models.errors import ObjectNotFoundError


def banners_list() -> list[Banner]:
    return list_banners()


def banners_list_active() -> list[Banner]:
    return list_active_banners()


def banners_get(identifier: int) -> Banner:
    banner = get_banner(identifier)
    if not banner:
        raise ObjectNotFoundError()
    return banner


def banners_create(banner: Banner, user) -> Banner:
    banner.created_by = user.id
    db_create(banner)
    track_activity(
        f'Banner #{banner.id} created ({banner.purpose})',
        ctx_less=True,
    )
    return banner


def banners_update(banner: Banner) -> Banner:
    # `updated_at` bumps automatically via `onupdate=func.now()`.
    db.session.commit()
    track_activity(f'Banner #{banner.id} updated', ctx_less=True)
    return banner


def banners_delete(banner: Banner) -> None:
    banner_id = banner.id
    db_delete(banner)
    track_activity(f'Banner #{banner_id} deleted', ctx_less=True)
