#  IRIS Source Code
#  Copyright (C) 2024 - DFIR-IRIS
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

from app import db
from app.datamgmt.filters.filters_db import get_filter_by_id
from app.models.errors import ObjectNotFoundError


def alert_filter_add(new_saved_filter):
    db.session.add(new_saved_filter)
    db.session.commit()


def alert_filter_get(identifier):
    alert_filter = get_filter_by_id(identifier)
    if not alert_filter:
        raise ObjectNotFoundError()
    return alert_filter


def alert_filter_update():
    db.session.commit()


def alert_filter_delete(saved_filter):
    db.session.delete(saved_filter)
    db.session.commit()
