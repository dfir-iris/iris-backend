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
from flask_sqlalchemy.query import Query
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import NoInspectionAvailable


class StableQuery(Query):
    """Query whose `paginate()` always has a deterministic row order.

    A LIMIT/OFFSET page over a query that is unordered — or ordered on a
    column with ties — has no stable row order. Each page is a separate
    execution and PostgreSQL may arrange the rows differently every time, so
    a record can be returned on two pages while another is never returned at
    all. Nothing about it looks wrong: the list renders, the total is right,
    and the missing rows are simply absent.

    This is not theoretical. Paging a 104-customer table 25 at a time returned
    104 rows containing 75 distinct customers — 29 were unreachable, and two
    consecutive pages consisted entirely of rows already served earlier.

    Appending the primary key is the fix, and it is applied here rather than
    at each call site because correctness used to be opt-in and most callers
    did not opt in: `parse_pagination_parameters` defaults `order_by` to None,
    and only 3 of 26 routes passed a default, so the common path issued no
    ORDER BY whatsoever. Two places had already been fixed individually
    (`alerts_db`, `managed_assets_db`) without the shared path being changed.
    """

    def paginate(self, *args, **kwargs):
        # Bind the framework implementation to the re-ordered query. Calling
        # `.paginate()` on it would re-enter this method.
        return Query.paginate(self._with_deterministic_order(), *args, **kwargs)

    def _with_deterministic_order(self):
        """Return this query with its entity's primary key appended.

        Returns the query untouched unless it selects exactly one whole mapped
        entity. That restriction is what makes this safe to apply globally:

        - `SELECT DISTINCT model.*` already contains the primary key, so
          adding it to the ORDER BY cannot raise "ORDER BY expressions must
          appear in select list".
        - A query built with `with_entities(Model.some_column)` reports
          `Model` as its entity but does not select the primary key, so it is
          skipped — ordering it here could raise under DISTINCT.
        - A query over a subquery or an aggregate exposes no mapped entity and
          is skipped. Those need ordering applied where they are built, since
          only the caller knows which column is unique.
        """
        descriptions = self.column_descriptions
        if len(descriptions) != 1:
            return self

        description = descriptions[0]
        entity = description.get('entity')
        # `expr is entity` distinguishes "select the whole entity" from
        # "select a column off it" — the latter reports the same entity.
        if entity is None or description.get('expr') is not entity:
            return self

        try:
            primary_key = sa_inspect(entity).primary_key
        except (NoInspectionAvailable, AttributeError):
            return self

        if not primary_key:
            return self

        # Appended, never substituted: any ordering the caller asked for still
        # takes precedence and this only breaks the ties within it. Harmless
        # if the caller already ordered by the primary key.
        return self.order_by(*primary_key)


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

# `query_class` makes StableQuery the base for `Model.query` and for
# `db.session.query(...)` — flask-sqlalchemy passes it through as the session's
# `query_cls`. Every paginated listing therefore gets a deterministic row order
# without each call site having to remember to ask for one.
db = SQLAlchemy(engine_options=SQLALCHEMY_ENGINE_OPTIONS, query_class=StableQuery)  # flask-sqlalchemy
