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

"""Queries the AI workflow runtime (agent loop and node executors) needs
while a node runs: the customers the identifiers of a tool call belong
to, the strictest chatbot policy of a set of customers, and the wait row
an async HTTP request commits before it sends.

Reads and writes go through their own connection, outside the session,
which may hold the uncommitted work of the running node.
"""

from sqlalchemy import func
from sqlalchemy import insert
from sqlalchemy import select
from sqlalchemy import update

from app.db import db
from app.models.ai_workflows import AiSuggestion
from app.models.ai_workflows import AiWorkflowWait
from app.models.ai_workflows import WAIT_CANCELLED
from app.models.ai_workflows import WAIT_PENDING
from app.models.alert_clusters import AlertCluster
from app.models.alerts import Alert
from app.models.cases import Cases
from app.models.chatbot_policy import ChatbotPolicy
from app.models.customers import Client
from app.models.war_rooms import WarRoomCase

_MAX_IDS = 1000


def _ids(values) -> list:
    return sorted({int(v) for v in values})[:_MAX_IDS]


def _mapping(statement) -> dict:
    with db.engine.connect() as connection:
        return {row[0]: row[1] for row in connection.execute(statement)}


def ai_workflows_runtime_db_case_customers(case_ids) -> dict:
    """{case_id: customer_id} of the cases that exist."""
    ids = _ids(case_ids)
    if not ids:
        return {}
    table = Cases.__table__
    return _mapping(select(table.c.case_id, table.c.client_id).where(table.c.case_id.in_(ids)))


def ai_workflows_runtime_db_alert_customers(alert_ids) -> dict:
    """{alert_id: customer_id} of the alerts that exist."""
    ids = _ids(alert_ids)
    if not ids:
        return {}
    table = Alert.__table__
    return _mapping(select(table.c.alert_id, table.c.alert_customer_id).where(table.c.alert_id.in_(ids)))


def ai_workflows_runtime_db_cluster_customers(cluster_ids) -> dict:
    """{cluster_id: customer_id} of the alert clusters that exist."""
    ids = _ids(cluster_ids)
    if not ids:
        return {}
    table = AlertCluster.__table__
    return _mapping(select(table.c.cluster_id, table.c.cluster_customer_id).where(table.c.cluster_id.in_(ids)))


def ai_workflows_runtime_db_war_room_case_ids(war_room_id) -> set:
    """Ids of the cases attached to a war room."""
    table = WarRoomCase.__table__
    with db.engine.connect() as connection:
        rows = connection.execute(select(table.c.case_id).where(table.c.war_room_id == int(war_room_id)))
        return {row[0] for row in rows}


def ai_workflows_runtime_db_customer_case_ids(customer_ids, limit=_MAX_IDS) -> list:
    """Ids of the cases of `customer_ids`, newest first."""
    ids = _ids(customer_ids)
    if not ids:
        return []
    table = Cases.__table__
    with db.engine.connect() as connection:
        rows = connection.execute(
            select(table.c.case_id).where(table.c.client_id.in_(ids)).order_by(table.c.case_id.desc()).limit(limit)
        )
        return [row[0] for row in rows]


def ai_workflows_runtime_db_strictest_policy(customer_ids=None):
    """Chatbot policy with the highest restriction level among the
    customers `customer_ids` (every customer when None); None when none
    of them has a policy."""
    query = (
        db.session.query(ChatbotPolicy)
        .join(Client, Client.chatbot_policy_id == ChatbotPolicy.id)
    )
    if customer_ids is not None:
        ids = _ids(customer_ids)
        if not ids:
            return None
        query = query.filter(Client.client_id.in_(ids))
    return query.order_by(ChatbotPolicy.restriction_level.desc()).first()


def ai_workflows_runtime_db_count_run_suggestions(run_id, exclude_kinds=()) -> int:
    """Committed suggestions of a run, but those of `exclude_kinds`."""
    table = AiSuggestion.__table__
    statement = select(func.count()).select_from(table).where(table.c.run_id == run_id)
    if exclude_kinds:
        statement = statement.where(table.c.kind.notin_(list(exclude_kinds)))
    with db.engine.connect() as connection:
        return int(connection.execute(statement).scalar() or 0)


def ai_workflows_runtime_db_create_wait(run_id, node_id, kind, wait_uuid, token_hash, expires_at) -> int:
    """Insert and commit a pending wait, so that an answer arriving
    before the node finishes finds it; returns its id."""
    table = AiWorkflowWait.__table__
    with db.engine.begin() as connection:
        return connection.execute(
            insert(table).values(uuid=wait_uuid, run_id=run_id, node_id=node_id, kind=kind, token_hash=token_hash,
                                 status=WAIT_PENDING, expires_at=expires_at).returning(table.c.id)
        ).scalar()


def ai_workflows_runtime_db_cancel_wait(wait_id) -> None:
    """Cancel a pre-created wait still pending (the request failed)."""
    table = AiWorkflowWait.__table__
    with db.engine.begin() as connection:
        connection.execute(
            update(table).where(table.c.id == wait_id, table.c.status == WAIT_PENDING).values(status=WAIT_CANCELLED)
        )
