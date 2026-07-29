#  IRIS Source Code
#  Copyright (C) 2025 - Airbus CyberSecurity (SAS)
#  ir@cyberactionlab.net
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

from functools import partial
import json
import collections

from flask_sqlalchemy import SQLAlchemy


SQLALCHEMY_ENGINE_OPTIONS = {
    "json_deserializer": partial(json.loads, object_pairs_hook=collections.OrderedDict),
    # `pool_pre_ping` catches connections that PostgreSQL closed on us
    # (idle timeouts, restarts). Cheap `SELECT 1` on checkout.
    "pool_pre_ping": True,
    # Recycle after 30 minutes. Longer than pgbouncer/idle-timeout
    # defaults but short enough that a poisoned connection can't linger
    # for a whole day.
    "pool_recycle": 1800,
    # `LIFO` pool means the most-recently-returned connection is checked
    # out next. Under gevent + psycogreen this reduces the odds of two
    # greenlets colliding on a shared connection: hot connections get
    # reused while idle ones sit until pool_recycle evicts them, so a
    # briefly-yielding query is unlikely to have another greenlet grab
    # its connection before it returns.
    "pool_use_lifo": True,
}

db = SQLAlchemy(engine_options=SQLALCHEMY_ENGINE_OPTIONS)  # flask-sqlalchemy
