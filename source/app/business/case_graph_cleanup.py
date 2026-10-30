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

"""Keeps the investigation graph consistent with the case objects.

Stored edges reference their endpoints polymorphically (`<kind>:<id>`),
so no foreign key removes them: the delete functions of assets, IOCs,
events, evidences, tasks and notes call `case_graph_cleanup_edges`
before deleting the object, so the edges go in the same transaction.
`case_graph_cleanup_notify` tells the open graphs of the case to
refresh after a case object changed (never raises).

Only depends on the persistence layer and the engine, so the asset / IOC
business modules can import it without an import cycle.
"""

from app.datamgmt.case.case_graph_db import case_graph_db_delete_edges_touching
from app.iris_engine.case_graph_live import case_graph_live_emit


def case_graph_cleanup_edges(case_id, kind, object_ids):
    """Delete (flushed, committed with the caller's delete) the stored
    edges touching `kind:object_ids`; the number deleted."""
    return case_graph_db_delete_edges_touching(kind, [int(object_id) for object_id in object_ids], case_id=case_id)


def case_graph_cleanup_notify(case_id, action, kind=None, object_id=None):
    """Push `case-graph-changed {entity: case_object}`; never raises."""
    if case_id is None:
        return
    key = f'{kind}:{object_id}' if kind and object_id else None
    case_graph_live_emit(case_id, 'case_object', action, key=key)
