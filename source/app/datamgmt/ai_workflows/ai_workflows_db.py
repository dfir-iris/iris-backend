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

"""Query helpers for the AI workflow, run ledger, suggestion and keystore
tables. Request-free — used from the REST layer, the hook listener and
the Celery tasks alike.
"""

import datetime

from sqlalchemy import and_
from sqlalchemy import func
from sqlalchemy import or_
from sqlalchemy import select
from sqlalchemy import text
from sqlalchemy import update

from app.db import db
from app.models.ai_workflows import AiKeystoreEntry
from app.models.ai_workflows import AiSuggestion
from app.models.ai_workflows import AiWorkflow
from app.models.ai_workflows import AiWorkflowInboundEvent
from app.models.ai_workflows import AiWorkflowLlmCall
from app.models.ai_workflows import AiWorkflowRun
from app.models.ai_workflows import AiWorkflowRunStep
from app.models.ai_workflows import AiWorkflowToolCall
from app.models.ai_workflows import AiWorkflowVersion
from app.models.ai_workflows import AiWorkflowWait
from app.models.ai_workflows import ENTITY_ALERT
from app.models.ai_workflows import ENTITY_ALERT_CLUSTER
from app.models.ai_workflows import ENTITY_CASE
from app.models.ai_workflows import ENTITY_WAR_ROOM
from app.models.ai_workflows import RUN_ACTIVE_STATUSES
from app.models.ai_workflows import RUN_RUNNING
from app.models.ai_workflows import RUN_SKIPPED
from app.models.ai_workflows import TRIGGER_EVENT
from app.models.ai_workflows import SUGGESTION_OPEN
from app.models.ai_workflows import WAIT_PENDING
from app.models.alert_clusters import AlertCluster
from app.models.alerts import Alert
from app.models.authorization import Group
from app.models.authorization import User
from app.models.authorization import UserGroup
from app.models.cases import Cases
from app.models.chatbot_policy import ChatbotPolicy
from app.models.customers import Client
from app.models.models import IrisHook
from app.models.war_rooms import WarRoom
from app.models.war_rooms import WarRoomMember


def ai_workflows_db_utcnow():
    """Naive UTC, matching the `DateTime` columns."""
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def ai_workflows_db_add(obj):
    """Add and flush, so the row has its id; the caller commits."""
    db.session.add(obj)
    db.session.flush()
    return obj


def ai_workflows_db_commit():
    db.session.commit()


def ai_workflows_db_rollback():
    db.session.rollback()


def ai_workflows_db_delete(obj):
    db.session.delete(obj)
    db.session.commit()


# ---- Workflows ------------------------------------------------------------

def ai_workflows_db_list(owner_id=None):
    query = AiWorkflow.query
    if owner_id is not None:
        query = query.filter(AiWorkflow.owner_id == owner_id)
    return query.order_by(AiWorkflow.name.asc(), AiWorkflow.id.asc()).all()


def ai_workflows_db_get(workflow_id):
    return AiWorkflow.query.filter(AiWorkflow.id == workflow_id).first()


def ai_workflows_db_get_by_uuid(workflow_uuid):
    return AiWorkflow.query.filter(AiWorkflow.uuid == workflow_uuid).first()


def ai_workflows_db_list_active(trigger_type):
    return (
        AiWorkflow.query
        .filter(AiWorkflow.is_active.is_(True), AiWorkflow.trigger_type == trigger_type)
        .order_by(AiWorkflow.id.asc())
        .all()
    )


def ai_workflows_db_list_versions(workflow_id):
    return (
        AiWorkflowVersion.query
        .filter(AiWorkflowVersion.workflow_id == workflow_id)
        .order_by(AiWorkflowVersion.version.desc())
        .all()
    )


def ai_workflows_db_get_version(workflow_id, version):
    return AiWorkflowVersion.query.filter(
        AiWorkflowVersion.workflow_id == workflow_id,
        AiWorkflowVersion.version == version,
    ).first()


def ai_workflows_db_run_counts(workflow_ids, since) -> dict:
    """{workflow_id: {status: count}} for runs started after `since`."""
    if not workflow_ids:
        return {}
    rows = (
        db.session.query(AiWorkflowRun.workflow_id, AiWorkflowRun.status, func.count(AiWorkflowRun.id))
        .filter(AiWorkflowRun.workflow_id.in_(workflow_ids), AiWorkflowRun.started_at >= since)
        .group_by(AiWorkflowRun.workflow_id, AiWorkflowRun.status)
        .all()
    )
    counts = {}
    for workflow_id, status, count in rows:
        counts.setdefault(workflow_id, {})[status] = count
    return counts


def ai_workflows_db_postload_hooks():
    """(hook_name, hook_description) of every postload hook."""
    return (
        IrisHook.query
        .with_entities(IrisHook.hook_name, IrisHook.hook_description)
        .filter(IrisHook.hook_name.like('on\\_postload\\_%'))
        .all()
    )


# ---- Runs -----------------------------------------------------------------

def ai_workflows_db_get_run(run_id, lock=False):
    query = AiWorkflowRun.query.filter(AiWorkflowRun.id == run_id)
    if lock:
        # Reload the row: a copy read earlier in the transaction is stale
        query = query.with_for_update().populate_existing()
    return query.first()


def ai_workflows_db_get_run_by_uuid(run_uuid):
    return AiWorkflowRun.query.filter(AiWorkflowRun.uuid == run_uuid).first()


def ai_workflows_db_list_runs(workflow_id=None, status=None, entity_type=None, entity_id=None,
                              user_id=None, page=1, per_page=50):
    """Runs, newest first. `user_id` limits to the runs the user owns
    the workflow of, runs as or triggered. Returns (rows, total)."""
    query = AiWorkflowRun.query
    if workflow_id is not None:
        query = query.filter(AiWorkflowRun.workflow_id == workflow_id)
    if status:
        query = query.filter(AiWorkflowRun.status == status)
    if entity_type:
        query = query.filter(AiWorkflowRun.entity_type == entity_type)
    if entity_id is not None:
        query = query.filter(AiWorkflowRun.entity_id == entity_id)
    if user_id is not None:
        owned = db.session.query(AiWorkflow.id).filter(AiWorkflow.owner_id == user_id)
        query = query.filter(or_(
            AiWorkflowRun.run_as_user_id == user_id,
            AiWorkflowRun.triggered_by_user_id == user_id,
            AiWorkflowRun.workflow_id.in_(owned),
        ))
    total = query.count()
    rows = (
        query.order_by(AiWorkflowRun.started_at.desc(), AiWorkflowRun.id.desc())
        .offset(max(page - 1, 0) * per_page)
        .limit(per_page)
        .all()
    )
    return rows, total


def ai_workflows_db_run_uuids(run_ids) -> dict:
    """{run_id: run uuid as str}."""
    ids = [i for i in set(run_ids or []) if i]
    if not ids:
        return {}
    rows = AiWorkflowRun.query.with_entities(AiWorkflowRun.id, AiWorkflowRun.uuid).filter(
        AiWorkflowRun.id.in_(ids)).all()
    return {r.id: str(r.uuid) for r in rows}


def ai_workflows_db_count_runs_since(workflow_id, since) -> int:
    """Runs toward the hourly cap: node tests are not."""
    return AiWorkflowRun.query.filter(
        AiWorkflowRun.workflow_id == workflow_id,
        AiWorkflowRun.started_at >= since,
        AiWorkflowRun.status != RUN_SKIPPED,
        AiWorkflowRun.tested_node_id.is_(None),
    ).count()


def ai_workflows_db_recent_dedup_run(workflow_id, dedup_key, since):
    return AiWorkflowRun.query.filter(
        AiWorkflowRun.workflow_id == workflow_id,
        AiWorkflowRun.dedup_key == dedup_key,
        AiWorkflowRun.started_at >= since,
        AiWorkflowRun.status != RUN_SKIPPED,
    ).first()


def ai_workflows_db_active_run_for_entity(workflow_id, entity_type, entity_id):
    return AiWorkflowRun.query.filter(
        AiWorkflowRun.workflow_id == workflow_id,
        AiWorkflowRun.entity_type == entity_type,
        AiWorkflowRun.entity_id == entity_id,
        AiWorkflowRun.status.in_(RUN_ACTIVE_STATUSES),
    ).first()


def ai_workflows_db_last_finished_run(workflow_id, entity_type, entity_id, exclude_run_id=None):
    query = AiWorkflowRun.query.filter(
        AiWorkflowRun.workflow_id == workflow_id,
        AiWorkflowRun.entity_type == entity_type,
        AiWorkflowRun.entity_id == entity_id,
        AiWorkflowRun.status.notin_(RUN_ACTIVE_STATUSES + (RUN_SKIPPED,)),
    )
    if exclude_run_id is not None:
        query = query.filter(AiWorkflowRun.id != exclude_run_id)
    return query.order_by(AiWorkflowRun.started_at.desc()).first()


_RECOVERY_BATCH = 200


def ai_workflows_db_stale_executing_runs(before):
    """(id, uuid) of runs a worker took and did not touch since `before`."""
    return (
        AiWorkflowRun.query.with_entities(AiWorkflowRun.id, AiWorkflowRun.uuid)
        .filter(
            AiWorkflowRun.is_executing.is_(True),
            AiWorkflowRun.executing_since < before,
            AiWorkflowRun.status.in_(RUN_ACTIVE_STATUSES),
        )
        .order_by(AiWorkflowRun.executing_since.asc())
        .limit(_RECOVERY_BATCH)
        .all()
    )


def ai_workflows_db_stuck_running_runs(before):
    """(id, uuid) of runs left `running` with work queued but no worker
    on them (lost broker message), not already requeued since `before`."""
    return (
        AiWorkflowRun.query.with_entities(AiWorkflowRun.id, AiWorkflowRun.uuid)
        .filter(
            AiWorkflowRun.is_executing.is_(False),
            AiWorkflowRun.status == RUN_RUNNING,
            AiWorkflowRun.updated_at < before,
            AiWorkflowRun.pending_nodes != text("'[]'::jsonb"),
            or_(AiWorkflowRun.requeued_at.is_(None), AiWorkflowRun.requeued_at < before),
        )
        .order_by(AiWorkflowRun.updated_at.asc())
        .limit(_RECOVERY_BATCH)
        .all()
    )


def ai_workflows_db_mark_requeued(run_id, now):
    """Stamp `requeued_at` (and nothing else: `updated_at` is kept) and
    commit."""
    db.session.execute(
        update(AiWorkflowRun)
        .where(AiWorkflowRun.id == run_id)
        .values(requeued_at=now, updated_at=AiWorkflowRun.updated_at)
        .execution_options(synchronize_session=False)
    )
    db.session.commit()


def ai_workflows_db_heartbeat_run(run_id, now) -> bool:
    """Refresh `executing_since` of an executing run in its own short
    transaction, outside the session (which may hold uncommitted work of
    the node). Gives up quickly rather than wait on a row lock. True when
    the row was updated."""
    with db.engine.begin() as connection:
        connection.execute(text("SET LOCAL lock_timeout = '2s'"))
        result = connection.execute(
            update(AiWorkflowRun.__table__)
            .where(AiWorkflowRun.__table__.c.id == run_id,
                   AiWorkflowRun.__table__.c.is_executing.is_(True),
                   AiWorkflowRun.__table__.c.status == RUN_RUNNING)
            .values(executing_since=now)
        )
        return bool(result.rowcount)


def ai_workflows_db_run_status_fresh(run_id):
    """Last committed status of a run, read outside the session (whose
    copy of the row may be stale); None when the run is gone."""
    with db.engine.connect() as connection:
        return connection.execute(
            select(AiWorkflowRun.__table__.c.status).where(AiWorkflowRun.__table__.c.id == run_id)
        ).scalar()


def ai_workflows_db_list_steps(run_id):
    return (
        AiWorkflowRunStep.query
        .filter(AiWorkflowRunStep.run_id == run_id)
        .order_by(AiWorkflowRunStep.seq.asc(), AiWorkflowRunStep.id.asc())
        .all()
    )


def ai_workflows_db_get_step(step_id):
    return AiWorkflowRunStep.query.filter(AiWorkflowRunStep.id == step_id).first()


def ai_workflows_db_list_tool_calls(run_id):
    return (
        AiWorkflowToolCall.query
        .filter(AiWorkflowToolCall.run_id == run_id)
        .order_by(AiWorkflowToolCall.id.asc())
        .all()
    )


def ai_workflows_db_list_llm_calls(run_id):
    return (
        AiWorkflowLlmCall.query
        .filter(AiWorkflowLlmCall.run_id == run_id)
        .order_by(AiWorkflowLlmCall.id.asc())
        .all()
    )


def ai_workflows_db_tokens_today(user_id=None) -> int:
    midnight = ai_workflows_db_utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    query = db.session.query(
        func.coalesce(func.sum(AiWorkflowLlmCall.prompt_tokens + AiWorkflowLlmCall.completion_tokens), 0)
    ).filter(AiWorkflowLlmCall.created_at >= midnight)
    if user_id is not None:
        query = query.filter(AiWorkflowLlmCall.user_id == user_id)
    return int(query.scalar() or 0)


# ---- Waits ----------------------------------------------------------------

def ai_workflows_db_get_wait_by_uuid(wait_uuid, lock=False):
    query = AiWorkflowWait.query.filter(AiWorkflowWait.uuid == wait_uuid)
    if lock:
        query = query.with_for_update()
    return query.first()


def ai_workflows_db_get_wait(wait_id, lock=False):
    query = AiWorkflowWait.query.filter(AiWorkflowWait.id == wait_id)
    if lock:
        query = query.with_for_update().populate_existing()
    return query.first()


def ai_workflows_db_list_waits(run_id):
    return AiWorkflowWait.query.filter(AiWorkflowWait.run_id == run_id).order_by(AiWorkflowWait.id.asc()).all()


def ai_workflows_db_pending_waits(run_id):
    return AiWorkflowWait.query.filter(
        AiWorkflowWait.run_id == run_id,
        AiWorkflowWait.status == WAIT_PENDING,
    ).all()


def ai_workflows_db_expired_waits(now):
    return AiWorkflowWait.query.filter(
        AiWorkflowWait.status == WAIT_PENDING,
        AiWorkflowWait.expires_at.isnot(None),
        AiWorkflowWait.expires_at <= now,
    ).order_by(AiWorkflowWait.expires_at.asc()).limit(500).all()


# ---- Suggestions ----------------------------------------------------------

def ai_workflows_db_get_suggestion(suggestion_id, lock=False):
    query = AiSuggestion.query.filter(AiSuggestion.id == suggestion_id)
    if lock:
        query = query.with_for_update()
    return query.first()


def ai_workflows_db_list_suggestions(entity_type=None, entity_id=None, statuses=None, run_id=None, limit=200):
    query = AiSuggestion.query
    if entity_type:
        query = query.filter(AiSuggestion.entity_type == entity_type)
    if entity_id is not None:
        query = query.filter(AiSuggestion.entity_id == entity_id)
    if statuses:
        query = query.filter(AiSuggestion.status.in_(statuses))
    if run_id is not None:
        query = query.filter(AiSuggestion.run_id == run_id)
    return query.order_by(AiSuggestion.created_at.desc(), AiSuggestion.id.desc()).limit(limit).all()


def ai_workflows_db_open_suggestions_for_wait(wait_id):
    return AiSuggestion.query.filter(
        AiSuggestion.wait_id == wait_id,
        AiSuggestion.status == SUGGESTION_OPEN,
    ).all()


# ---- Keystore -------------------------------------------------------------

def ai_workflows_db_user_group_ids(user_id) -> list:
    rows = UserGroup.query.with_entities(UserGroup.group_id).filter(UserGroup.user_id == user_id).all()
    return [r[0] for r in rows]


def ai_workflows_db_keystore_list(owner_id=None, include_shared=True):
    """Personal entries of `owner_id` plus, optionally, every shared one."""
    clauses = []
    if owner_id is not None:
        clauses.append(and_(AiKeystoreEntry.scope == 'personal', AiKeystoreEntry.owner_id == owner_id))
    if include_shared:
        clauses.append(AiKeystoreEntry.scope == 'shared')
    if not clauses:
        return []
    return AiKeystoreEntry.query.filter(or_(*clauses)).order_by(AiKeystoreEntry.name.asc()).all()


def ai_workflows_db_keystore_list_all():
    return AiKeystoreEntry.query.order_by(AiKeystoreEntry.name.asc()).all()


def ai_workflows_db_keystore_get(entry_id):
    return AiKeystoreEntry.query.filter(AiKeystoreEntry.id == entry_id).first()


def ai_workflows_db_keystore_by_name(name):
    return AiKeystoreEntry.query.filter(AiKeystoreEntry.name == name).order_by(AiKeystoreEntry.id.asc()).all()


# ---- Inbound log ----------------------------------------------------------

def ai_workflows_db_list_inbound_events(workflow_id=None, limit=100):
    query = AiWorkflowInboundEvent.query
    if workflow_id is not None:
        query = query.filter(AiWorkflowInboundEvent.workflow_id == workflow_id)
    return query.order_by(AiWorkflowInboundEvent.created_at.desc()).limit(limit).all()


# ---- Retention ------------------------------------------------------------

_PRUNE_BATCH = 5000
_KEPT_VERSIONS = 50


def _delete_in_batches(model, ids_select) -> int:
    """Delete the rows of `model` whose id `ids_select` (a select of at
    most `_PRUNE_BATCH` ids) returns, a batch per transaction, until none
    is left: short locks, bounded WAL per commit."""
    total = 0
    while True:
        deleted = db.session.execute(
            model.__table__.delete().where(model.__table__.c.id.in_(ids_select))
        ).rowcount or 0
        db.session.commit()
        total += deleted
        if deleted < _PRUNE_BATCH:
            return total


def _old_version_ids():
    ranked = select(
        AiWorkflowVersion.id.label('id'),
        func.row_number().over(partition_by=AiWorkflowVersion.workflow_id,
                               order_by=AiWorkflowVersion.version.desc()).label('rank'),
    ).subquery()
    return select(ranked.c.id).where(ranked.c.rank > _KEPT_VERSIONS).limit(_PRUNE_BATCH)


def ai_workflows_db_prune(before) -> dict:
    """Delete finished runs (their steps, tool and LLM calls cascade),
    tool / LLM calls of no run (accepted suggestions), closed suggestions
    and inbound logs older than `before`, and the definition versions of
    each workflow beyond the newest `_KEPT_VERSIONS`. Batched; commits."""
    counts = {
        'runs': _delete_in_batches(AiWorkflowRun, select(AiWorkflowRun.id).where(
            AiWorkflowRun.status.notin_(RUN_ACTIVE_STATUSES),
            AiWorkflowRun.started_at < before,
        ).limit(_PRUNE_BATCH)),
        'tool_calls': _delete_in_batches(AiWorkflowToolCall, select(AiWorkflowToolCall.id).where(
            AiWorkflowToolCall.run_id.is_(None),
            AiWorkflowToolCall.created_at < before,
        ).limit(_PRUNE_BATCH)),
        'llm_calls': _delete_in_batches(AiWorkflowLlmCall, select(AiWorkflowLlmCall.id).where(
            AiWorkflowLlmCall.run_id.is_(None),
            AiWorkflowLlmCall.created_at < before,
        ).limit(_PRUNE_BATCH)),
        'suggestions': _delete_in_batches(AiSuggestion, select(AiSuggestion.id).where(
            AiSuggestion.status != SUGGESTION_OPEN,
            AiSuggestion.created_at < before,
        ).limit(_PRUNE_BATCH)),
        'inbound_events': _delete_in_batches(AiWorkflowInboundEvent, select(AiWorkflowInboundEvent.id).where(
            AiWorkflowInboundEvent.created_at < before,
        ).limit(_PRUNE_BATCH)),
        'versions': _delete_in_batches(AiWorkflowVersion, _old_version_ids()),
    }
    return counts


# ---- Entities -------------------------------------------------------------

def ai_workflows_db_get_user(user_id):
    return User.query.filter(User.id == user_id).first()


def ai_workflows_db_user_summary(user_ids) -> dict:
    ids = [i for i in set(user_ids or []) if i]
    if not ids:
        return {}
    rows = User.query.with_entities(User.id, User.user, User.name).filter(User.id.in_(ids)).all()
    return {r.id: {'id': r.id, 'login': r.user, 'name': r.name} for r in rows}


def ai_workflows_db_entity_customer(entity_type, entity_id):
    """Customer id of an entity; None for war rooms, which have none."""
    if entity_id is None:
        return None
    if entity_type == ENTITY_ALERT:
        row = Alert.query.with_entities(Alert.alert_customer_id).filter(Alert.alert_id == entity_id).first()
    elif entity_type == ENTITY_ALERT_CLUSTER:
        row = AlertCluster.query.with_entities(AlertCluster.cluster_customer_id).filter(
            AlertCluster.cluster_id == entity_id).first()
    elif entity_type == ENTITY_CASE:
        row = Cases.query.with_entities(Cases.client_id).filter(Cases.case_id == entity_id).first()
    else:
        return None
    return row[0] if row else None


def ai_workflows_db_entity_exists(entity_type, entity_id) -> bool:
    if entity_type == ENTITY_ALERT:
        return Alert.query.filter(Alert.alert_id == entity_id).count() > 0
    if entity_type == ENTITY_ALERT_CLUSTER:
        return AlertCluster.query.filter(AlertCluster.cluster_id == entity_id).count() > 0
    if entity_type == ENTITY_CASE:
        return Cases.query.filter(Cases.case_id == entity_id).count() > 0
    if entity_type == ENTITY_WAR_ROOM:
        return WarRoom.query.filter(WarRoom.war_room_id == entity_id).count() > 0
    return False


def ai_workflows_db_entity_title(entity_type, entity_id):
    if entity_type == ENTITY_ALERT:
        row = Alert.query.with_entities(Alert.alert_title).filter(Alert.alert_id == entity_id).first()
    elif entity_type == ENTITY_ALERT_CLUSTER:
        row = AlertCluster.query.with_entities(AlertCluster.cluster_title).filter(
            AlertCluster.cluster_id == entity_id).first()
    elif entity_type == ENTITY_CASE:
        row = Cases.query.with_entities(Cases.name).filter(Cases.case_id == entity_id).first()
    elif entity_type == ENTITY_WAR_ROOM:
        row = WarRoom.query.with_entities(WarRoom.name).filter(WarRoom.war_room_id == entity_id).first()
    else:
        return None
    return row[0] if row else None


def ai_workflows_db_alert_cluster_summary(cluster_id):
    cluster = AlertCluster.query.filter(AlertCluster.cluster_id == cluster_id).first()
    if cluster is None:
        return None
    return {
        'cluster_id': cluster.cluster_id,
        'title': cluster.cluster_title,
        'description': cluster.cluster_description,
        'status_id': cluster.cluster_status_id,
        'severity_id': cluster.cluster_severity_id,
        'customer_id': cluster.cluster_customer_id,
        'owner_id': cluster.cluster_owner_id,
        'case_id': cluster.cluster_case_id,
        'created_at': cluster.cluster_creation_time.isoformat() if cluster.cluster_creation_time else None,
        'alert_ids': [a.alert_id for a in cluster.alerts][:200],
    }


def ai_workflows_db_entity_owner_ids(entity_type, entity_id) -> list:
    """Users an entity-audience suggestion notifies: the alert / cluster /
    case owner, or the war room members."""
    if entity_type == ENTITY_ALERT:
        row = Alert.query.with_entities(Alert.alert_owner_id).filter(Alert.alert_id == entity_id).first()
        return [row[0]] if row and row[0] else []
    if entity_type == ENTITY_ALERT_CLUSTER:
        row = AlertCluster.query.with_entities(AlertCluster.cluster_owner_id).filter(
            AlertCluster.cluster_id == entity_id).first()
        return [row[0]] if row and row[0] else []
    if entity_type == ENTITY_CASE:
        row = Cases.query.with_entities(Cases.owner_id).filter(Cases.case_id == entity_id).first()
        return [row[0]] if row and row[0] else []
    if entity_type == ENTITY_WAR_ROOM:
        rows = WarRoomMember.query.with_entities(WarRoomMember.user_id).filter(
            WarRoomMember.war_room_id == entity_id).all()
        return [r[0] for r in rows]
    return []


def ai_workflows_db_open_war_room_ids(limit, after_id=None):
    """Open war room ids, ascending; `after_id` pages."""
    query = WarRoom.query.with_entities(WarRoom.war_room_id).filter(
        WarRoom.archived_at.is_(None), WarRoom.state != 'closed')
    if after_id is not None:
        query = query.filter(WarRoom.war_room_id > after_id)
    rows = query.order_by(WarRoom.war_room_id.asc()).limit(limit).all()
    return [r[0] for r in rows]


def ai_workflows_db_open_case_ids(limit, customer_ids=None, after_id=None):
    """Open case ids, newest first; `after_id` pages."""
    query = Cases.query.with_entities(Cases.case_id).filter(Cases.close_date.is_(None))
    if customer_ids:
        query = query.filter(Cases.client_id.in_(customer_ids))
    if after_id is not None:
        query = query.filter(Cases.case_id < after_id)
    rows = query.order_by(Cases.case_id.desc()).limit(limit).all()
    return [r[0] for r in rows]


def ai_workflows_db_policy_for_customer(customer_id):
    if customer_id is None:
        return None
    return (
        db.session.query(ChatbotPolicy)
        .join(Client, Client.chatbot_policy_id == ChatbotPolicy.id)
        .filter(Client.client_id == customer_id)
        .first()
    )


# ---- Keystore (writes) ----------------------------------------------------

def ai_workflows_db_keystore_touch(entry):
    """Stamp `last_used_at`; flush only, the caller commits."""
    entry.last_used_at = ai_workflows_db_utcnow()
    db.session.flush()


def ai_workflows_db_existing_group_ids(group_ids) -> list:
    ids = [i for i in set(group_ids or []) if i is not None]
    if not ids:
        return []
    rows = Group.query.with_entities(Group.group_id).filter(Group.group_id.in_(ids)).all()
    return [r[0] for r in rows]


# ---- Business / REST helpers ----------------------------------------------

def ai_workflows_db_active_runs_for_workflow(workflow_id):
    return AiWorkflowRun.query.filter(
        AiWorkflowRun.workflow_id == workflow_id,
        AiWorkflowRun.status.in_(RUN_ACTIVE_STATUSES),
    ).all()


def ai_workflows_db_open_suggestions_for_entities(entity_type, entity_ids, limit=5000):
    if not entity_ids:
        return []
    return AiSuggestion.query.filter(
        AiSuggestion.entity_type == entity_type,
        AiSuggestion.entity_id.in_(entity_ids),
        AiSuggestion.status == SUGGESTION_OPEN,
    ).limit(limit).all()


def ai_workflows_db_list_inbound_events_for_workflows(workflow_ids, limit=100):
    if not workflow_ids:
        return []
    return (
        AiWorkflowInboundEvent.query
        .filter(AiWorkflowInboundEvent.workflow_id.in_(workflow_ids))
        .order_by(AiWorkflowInboundEvent.created_at.desc(), AiWorkflowInboundEvent.id.desc())
        .limit(limit)
        .all()
    )


def ai_workflows_db_list_inbound_events_for_run(run_id, limit=500):
    return (
        AiWorkflowInboundEvent.query
        .filter(AiWorkflowInboundEvent.run_id == run_id)
        .order_by(AiWorkflowInboundEvent.id.asc())
        .limit(limit)
        .all()
    )


def ai_workflows_db_owned_workflow_ids(owner_id) -> list:
    rows = AiWorkflow.query.with_entities(AiWorkflow.id).filter(AiWorkflow.owner_id == owner_id).all()
    return [r[0] for r in rows]


# ---- Engine helpers -------------------------------------------------------

def ai_workflows_db_entity_row(entity_type, entity_id):
    """The ORM row of an alert, case or war room (clusters go through
    `ai_workflows_db_alert_cluster_summary`)."""
    if entity_id is None:
        return None
    if entity_type == ENTITY_ALERT:
        return Alert.query.filter(Alert.alert_id == entity_id).first()
    if entity_type == ENTITY_CASE:
        return Cases.query.filter(Cases.case_id == entity_id).first()
    if entity_type == ENTITY_WAR_ROOM:
        return WarRoom.query.filter(WarRoom.war_room_id == entity_id).first()
    return None


def ai_workflows_db_next_step_seq(run_id) -> int:
    current = db.session.query(func.max(AiWorkflowRunStep.seq)).filter(AiWorkflowRunStep.run_id == run_id).scalar()
    return int(current or 0) + 1


def ai_workflows_db_recover_session():
    """Roll back only when a failed flush / commit left the session
    unusable, so pending work of the caller is kept otherwise."""
    if not db.session.is_active:
        db.session.rollback()


def ai_workflows_db_lock_workflow(workflow_id):
    """Lock (and reload) the workflow row until the caller commits:
    serialises the trigger suppression checks with the run insert."""
    return AiWorkflow.query.filter(AiWorkflow.id == workflow_id).with_for_update().populate_existing().first()


def ai_workflows_db_count_skip(workflow_id, reason, now):
    """Count a suppressed trigger on the workflow (no run row). Leaves
    `updated_at` alone; the caller commits."""
    db.session.execute(
        update(AiWorkflow)
        .where(AiWorkflow.id == workflow_id)
        .values(skipped_count=AiWorkflow.skipped_count + 1,
                last_skip_reason=(reason or '')[:64] or None,
                last_skipped_at=now,
                updated_at=AiWorkflow.updated_at)
        .execution_options(synchronize_session=False)
    )


def ai_workflows_db_active_event_hooks() -> list:
    """(workflow id, configured hooks) of the active event workflows,
    without loading their graphs."""
    return (
        db.session.query(AiWorkflow.id, AiWorkflow.trigger_config['hooks'])
        .filter(AiWorkflow.is_active.is_(True), AiWorkflow.trigger_type == TRIGGER_EVENT)
        .order_by(AiWorkflow.id.asc())
        .all()
    )


def ai_workflows_db_settle_session() -> bool:
    """Commit what the session holds after a node ran; roll back when
    that is impossible. True when uncommitted work was lost."""
    if db.session.is_active:
        try:
            db.session.commit()
            return False
        except Exception:
            db.session.rollback()
            return True
    db.session.rollback()
    return True


def ai_workflows_db_steps_with_status(run_id, status):
    return AiWorkflowRunStep.query.filter(
        AiWorkflowRunStep.run_id == run_id,
        AiWorkflowRunStep.status == status,
    ).all()
