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

"""Query helpers used only by the AI workflow business modules (REST
surface, inbound endpoints, user deletion)."""

from sqlalchemy import and_
from sqlalchemy import func
from sqlalchemy import or_

from app.db import db
from app.models.ai_workflows import AiWorkflow
from app.models.ai_workflows import AiWorkflowInboundEvent
from app.models.ai_workflows import AiWorkflowRun
from app.models.ai_workflows import AiWorkflowRunStep
from app.models.ai_workflows import STEP_RESUMED


def ai_workflows_business_db_count_owned(user_id) -> int:
    """Number of AI workflows `user_id` owns."""
    return AiWorkflow.query.filter(AiWorkflow.owner_id == user_id).count()


def ai_workflows_business_db_signature_seen(workflow_id, signature_sha256, since) -> bool:
    """Whether an inbound event of the workflow already carried this
    signature (sha256 of it) since `since`."""
    return db.session.query(AiWorkflowInboundEvent.id).filter(
        AiWorkflowInboundEvent.workflow_id == workflow_id,
        AiWorkflowInboundEvent.signature_sha256 == signature_sha256,
        AiWorkflowInboundEvent.created_at >= since,
    ).first() is not None


def _run_filters(query, workflow_id, status, entity_type, entity_id):
    if workflow_id is not None:
        query = query.filter(AiWorkflowRun.workflow_id == workflow_id)
    if status:
        query = query.filter(AiWorkflowRun.status == status)
    if entity_type:
        query = query.filter(AiWorkflowRun.entity_type == entity_type)
    if entity_id is not None:
        query = query.filter(AiWorkflowRun.entity_id == entity_id)
    return query


def ai_workflows_business_db_involved_runs(user_id, workflow_id=None, status=None, entity_type=None,
                                           entity_id=None, limit=5000) -> list:
    """Light rows `(id, entity_type, entity_id)` of the runs `user_id` is
    involved in (owner at run start — the current workflow owner for
    runs that predate `owner_id` —, run-as or trigger user), newest
    first, at most `limit`."""
    query = db.session.query(AiWorkflowRun.id, AiWorkflowRun.entity_type, AiWorkflowRun.entity_id)
    query = _run_filters(query, workflow_id, status, entity_type, entity_id)
    query = query.filter(_involved(user_id))
    return query.order_by(AiWorkflowRun.started_at.desc(), AiWorkflowRun.id.desc()).limit(limit).all()


def _involved(user_id):
    """The runs `user_id` is involved in, as a filter clause."""
    owned = db.session.query(AiWorkflow.id).filter(AiWorkflow.owner_id == user_id)
    return or_(
        AiWorkflowRun.owner_id == user_id,
        and_(AiWorkflowRun.owner_id.is_(None), AiWorkflowRun.workflow_id.in_(owned)),
        AiWorkflowRun.run_as_user_id == user_id,
        AiWorkflowRun.triggered_by_user_id == user_id,
    )


def ai_workflows_business_db_runs_by_ids(run_ids) -> list:
    """The runs, in the order of `run_ids`."""
    ids = [i for i in run_ids or [] if i is not None]
    if not ids:
        return []
    rows = {r.id: r for r in AiWorkflowRun.query.filter(AiWorkflowRun.id.in_(ids)).all()}
    return [rows[i] for i in ids if i in rows]


def _node_steps(workflow_id, involved_user_id=None):
    """Steps of the runs of the workflow, without the `resumed` rows (a
    wait resolving is not a new event of its node) nor the node tests;
    with `involved_user_id`, only of the runs that user is involved in."""
    query = (AiWorkflowRunStep.query
             .join(AiWorkflowRun, AiWorkflowRun.id == AiWorkflowRunStep.run_id)
             .filter(AiWorkflowRun.workflow_id == workflow_id, AiWorkflowRunStep.status != STEP_RESUMED,
                     AiWorkflowRun.tested_node_id.is_(None)))
    if involved_user_id is not None:
        query = query.filter(_involved(involved_user_id))
    return query


def ai_workflows_business_db_node_stats(workflow_id, involved_user_id=None) -> dict:
    """{node_id: {'counts': {status: count}, 'last_at': datetime}} over
    every run of the workflow (`involved_user_id`: see `_node_steps`)."""
    rows = (
        _node_steps(workflow_id, involved_user_id)
        .with_entities(AiWorkflowRunStep.node_id, AiWorkflowRunStep.status, func.count(AiWorkflowRunStep.id),
                       func.max(AiWorkflowRunStep.started_at))
        .group_by(AiWorkflowRunStep.node_id, AiWorkflowRunStep.status)
        .all()
    )
    stats = {}
    for node_id, status, count, last_at in rows:
        entry = stats.setdefault(node_id, {'counts': {}, 'last_at': None})
        entry['counts'][status] = count
        if last_at is not None and (entry['last_at'] is None or last_at > entry['last_at']):
            entry['last_at'] = last_at
    return stats


def ai_workflows_business_db_node_step_candidates(workflow_id, node_id, status=None, limit=5000,
                                                  involved_user_id=None) -> list:
    """Light rows `(step_id, entity_type, entity_id)` of the steps of
    `node_id` in the runs of the workflow, newest first, at most `limit`
    (`involved_user_id`: see `_node_steps`)."""
    query = (
        _node_steps(workflow_id, involved_user_id)
        .filter(AiWorkflowRunStep.node_id == node_id)
        .with_entities(AiWorkflowRunStep.id, AiWorkflowRun.entity_type, AiWorkflowRun.entity_id)
    )
    if status:
        query = query.filter(AiWorkflowRunStep.status == status)
    return query.order_by(AiWorkflowRunStep.started_at.desc(), AiWorkflowRunStep.id.desc()).limit(limit).all()


def ai_workflows_business_db_steps_by_ids(step_ids) -> list:
    """The steps, in the order of `step_ids`."""
    ids = [i for i in step_ids or [] if i is not None]
    if not ids:
        return []
    rows = {s.id: s for s in AiWorkflowRunStep.query.filter(AiWorkflowRunStep.id.in_(ids)).all()}
    return [rows[i] for i in ids if i in rows]
