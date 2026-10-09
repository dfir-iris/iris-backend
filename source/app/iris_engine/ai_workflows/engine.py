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

"""The run state machine.

A run is a queue of node ids (`pending_nodes`). A Celery step task
takes the run (`is_executing`), pops nodes one at a time, executes each
as the run-as user and records it as a step, then pushes the nodes wired
to the port the node left through. Every transition is committed, so a
run survives worker restarts; a node is popped (committed) before it
executes, so a worker lost mid-node never executes it twice — the tick
marks such a step failed instead.

A node that waits (async HTTP, a question, a delay) parks an
`AiWorkflowWait` and its branch stops there; the run is `waiting` once
nothing else is pending. `ai_workflows_engine_resume_wait` (callback,
answer, accepted suggestion, expiry) records a `resumed` step and
continues the branch.

Identity: a run always acts as the workflow owner (`run_as_user_id`),
whoever triggered it, bounded by the API-key scope of the triggering
credential (`scope_mask`). The owner's access to the run entity is
checked before the run starts and again before every node, so a run
whose owner lost access (or MCP rights) fails instead of going on.

Locks: always the run row first, then its waits
(`ai_workflows_engine_lock_run_then_wait`).
"""

import datetime
import hashlib
import json
import logging
import secrets
import uuid

from flask import current_app

from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_add
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_commit
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_count_skip
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_get_run
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_get_step
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_get_user
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_get_wait
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_get_wait_by_uuid
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_mark_requeued
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_next_step_seq
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_open_suggestions_for_wait
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_pending_waits
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_rollback
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_settle_session
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_steps_with_status
from app.datamgmt.ai_workflows.ai_workflows_db import ai_workflows_db_utcnow
from app.iris_engine.ai_workflows.context import ai_workflows_context_initial
from app.iris_engine.ai_workflows.entities import ai_workflows_entities_customer
from app.iris_engine.ai_workflows.entities import ai_workflows_entities_scope_allows
from app.iris_engine.ai_workflows.entities import ai_workflows_entities_snapshot
from app.iris_engine.ai_workflows.entities import ai_workflows_entities_user_can_access
from app.iris_engine.ai_workflows.graph import ai_workflows_graph_nodes
from app.iris_engine.ai_workflows.graph import ai_workflows_graph_targets
from app.iris_engine.ai_workflows.graph import ai_workflows_graph_trigger_id
from app.iris_engine.ai_workflows.identity import AiWorkflowIdentityError
from app.iris_engine.ai_workflows.identity import ai_workflows_identity
from app.iris_engine.ai_workflows.identity import ai_workflows_identity_chain
from app.iris_engine.ai_workflows.identity import ai_workflows_identity_check_user
from app.iris_engine.ai_workflows.keystore import ai_workflows_keystore_resolver
from app.iris_engine.ai_workflows.nodes import NODE_PORTS
from app.iris_engine.ai_workflows.nodes import PORT_ANSWERED
from app.iris_engine.ai_workflows.nodes import PORT_ERROR
from app.iris_engine.ai_workflows.nodes import PORT_OUT
from app.iris_engine.ai_workflows.nodes import PORT_TIMEOUT
from app.iris_engine.ai_workflows.nodes import NodeContext
from app.iris_engine.ai_workflows.nodes import ai_workflows_nodes_execute
from app.iris_engine.ai_workflows.suggestions import ai_workflows_suggestions_create
from app.iris_engine.ai_workflows.suggestions import ai_workflows_suggestions_emit
from app.iris_engine.ai_workflows.tools import ai_workflows_tools_json_safe
from app.models.ai_workflows import AiWorkflowRun
from app.models.ai_workflows import AiWorkflowRunStep
from app.models.ai_workflows import AiWorkflowWait
from app.models.ai_workflows import ENTITY_CASE
from app.models.ai_workflows import ENTITY_TYPES
from app.models.ai_workflows import RUN_ACTIVE_STATUSES
from app.models.ai_workflows import RUN_CANCELLED
from app.models.ai_workflows import RUN_FAILED
from app.models.ai_workflows import RUN_RUNNING
from app.models.ai_workflows import RUN_SKIPPED
from app.models.ai_workflows import RUN_SUCCEEDED
from app.models.ai_workflows import RUN_WAITING
from app.models.ai_workflows import STEP_FAILED
from app.models.ai_workflows import STEP_RESUMED
from app.models.ai_workflows import STEP_SUCCEEDED
from app.models.ai_workflows import STEP_WAITING
from app.models.ai_workflows import SUGGESTION_EXPIRED
from app.models.ai_workflows import WAIT_CALLBACK
from app.models.ai_workflows import WAIT_CANCELLED
from app.models.ai_workflows import WAIT_DELAY
from app.models.ai_workflows import WAIT_EXPIRED
from app.models.ai_workflows import WAIT_PENDING
from app.models.ai_workflows import WAIT_RESOLVED
from app.models.ai_workflows import WAIT_USER_INPUT

logger = logging.getLogger(__name__)

# A step row while its node executes (the model statuses are final ones)
STEP_RUNNING = 'running'

# Async HTTP nodes commit their callback wait before sending, and `_park`
# adopts it (`spec['wait_id']`), so a callback faster than the node is
# not refused
AI_WORKFLOWS_ENGINE_ADOPTS_PREPARED_WAITS = True

# Nodes one step task executes before handing the run back to the queue
_NODES_PER_TASK = 25
_MAX_ERROR = 4000

_SNAPSHOT_FIELDS = ('name', 'description', 'version', 'trigger_type', 'trigger_config', 'customer_scope', 'graph',
                    'owner_id', 'write_tool_allowlist', 'max_runs_per_hour', 'token_budget_per_run',
                    'suggestion_audience')


# ---- Tokens and public URLs ----------------------------------------------------

def ai_workflows_engine_new_token() -> str:
    return secrets.token_urlsafe(32)


def ai_workflows_engine_hash_token(token) -> str:
    return hashlib.sha256((token or '').encode('utf-8')).hexdigest()


def _public_base_url() -> str:
    base = current_app.config.get('AI_WORKFLOWS_CALLBACK_BASE_URL') or current_app.config.get('IRIS_ALLOW_ORIGIN') \
        or ''
    if isinstance(base, (list, tuple)):
        base = base[0] if base else ''
    base = str(base).replace(',', ' ').split()
    return base[0].rstrip('/') if base else ''


def ai_workflows_engine_callback_url(wait_uuid) -> str:
    return f'{_public_base_url()}/api/v2/ai-workflows/callbacks/{wait_uuid}'


def ai_workflows_engine_inbound_url(workflow_uuid) -> str:
    return f'{_public_base_url()}/api/v2/ai-workflows/hooks/{workflow_uuid}'


# ---- Helpers ----------------------------------------------------------------------

def _config(name, default):
    try:
        return int(current_app.config.get(name, default))
    except (TypeError, ValueError):
        return default


def _iso(value):
    return value.isoformat() if value is not None else None


def _sha256(payload):
    try:
        raw = json.dumps(payload, sort_keys=True, default=str)
    except (TypeError, ValueError):
        raw = str(payload)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _snapshot(workflow) -> dict:
    snapshot = {name: getattr(workflow, name, None) for name in _SNAPSHOT_FIELDS}
    snapshot['id'] = workflow.id
    snapshot['uuid'] = str(workflow.uuid) if getattr(workflow, 'uuid', None) else None
    return ai_workflows_tools_json_safe(snapshot)


def _enqueue(run_id, countdown=None):
    """Hand the run to a worker. A lost message is recovered by the tick."""
    from app.iris_engine.ai_workflows.tasks import ai_workflows_task_step
    try:
        ai_workflows_task_step.apply_async(args=[run_id], countdown=countdown)
    except Exception:
        logger.exception(f'Could not enqueue AI workflow run #{run_id}; the tick will pick it up')


def _set_context(run, **updates):
    """JSONB columns are only saved when reassigned."""
    context = dict(run.context or {})
    context.update(updates)
    run.context = context


def _store_node(run, node_id, output, port):
    nodes = dict((run.context or {}).get('nodes') or {})
    nodes[node_id] = {'output': output, 'port': port}
    _set_context(run, nodes=nodes, last=node_id)


def _push(run, graph, node_id, port):
    targets = ai_workflows_graph_targets(graph, node_id, port) if port else []
    if targets:
        run.pending_nodes = list(run.pending_nodes or []) + targets
    return targets


def _graph(run) -> dict:
    return (run.definition_snapshot or {}).get('graph') or {}


def ai_workflows_engine_access_denial(workflow, run_as_user_id, entity_type, entity_id, scope_mask=None):
    """Why a run of `workflow` acting as `run_as_user_id` may not start on
    the entity (None: it may). Checked before anything else about the
    trigger, so a refused run says nothing about the trigger condition or
    the suppression rules. May commit (access caches are materialised)."""
    user = ai_workflows_db_get_user(run_as_user_id) if run_as_user_id else None
    try:
        ai_workflows_identity_check_user(user)
    except AiWorkflowIdentityError as e:
        return str(e)
    if entity_type is not None and entity_type not in ENTITY_TYPES:
        return f'Unknown entity type {entity_type}'
    if entity_type and entity_id is not None:
        if not ai_workflows_entities_scope_allows(entity_type, scope_mask):
            return f'The API key that started the run may not read {entity_type} objects'
        if not ai_workflows_entities_user_can_access(run_as_user_id, entity_type, entity_id):
            return f'The run-as user cannot access {entity_type} #{entity_id}'
        scope = workflow.customer_scope or []
        if scope:
            customer_id = ai_workflows_entities_customer(entity_type, entity_id)
            if customer_id is not None and customer_id not in scope:
                return f'{entity_type} #{entity_id} belongs to a customer outside the workflow scope'
    if ai_workflows_graph_trigger_id(workflow.graph) is None:
        return 'The workflow has no trigger node'
    return None


def _new_run(workflow, trigger_type, *, run_as_user_id, triggered_by_user_id, entity_type, entity_id, sub_entity,
             payload, dry_run, dedup_key, chain_depth, parent_run_id, scope_mask, minimal=False) -> AiWorkflowRun:
    """A run row; `minimal` (a refused run) keeps no definition, payload
    or context, so refused triggers cost a small row only."""
    payload = ai_workflows_tools_json_safe(payload) if payload is not None and not minimal else None
    return AiWorkflowRun(
        uuid=uuid.uuid4(),
        workflow_id=workflow.id,
        workflow_name=workflow.name,
        workflow_version=workflow.version or 1,
        definition_snapshot={} if minimal else _snapshot(workflow),
        status=RUN_RUNNING,
        trigger_type=trigger_type,
        trigger_payload=payload,
        context={} if minimal else ai_workflows_context_initial(trigger_type, payload, entity_type, entity_id,
                                                                sub_entity),
        pending_nodes=[],
        is_executing=False,
        entity_type=entity_type or None,
        entity_id=entity_id if entity_type else None,
        sub_entity=sub_entity,
        run_as_user_id=run_as_user_id,
        owner_id=workflow.owner_id,
        scope_mask=int(scope_mask) if scope_mask is not None else None,
        triggered_by_user_id=triggered_by_user_id,
        dedup_key=(str(dedup_key)[:255] if dedup_key else None),
        chain_depth=int(chain_depth or 0),
        parent_run_id=parent_run_id,
        is_dry_run=bool(dry_run),
        used_key_names=[],
        tokens_used=0,
        step_count=0,
    )


# ---- Starting -----------------------------------------------------------------------

def _owner_identity(workflow, run_as_user_id):
    """Runs always act as the workflow owner, whoever started them."""
    if run_as_user_id is not None and run_as_user_id != workflow.owner_id:
        logger.warning(f'AI workflow #{workflow.id}: a run requested as user #{run_as_user_id} acts as the '
                       f'workflow owner #{workflow.owner_id}')
    return workflow.owner_id


def ai_workflows_engine_start_run(workflow, trigger_type, *, run_as_user_id, triggered_by_user_id=None,
                                  entity_type=None, entity_id=None, sub_entity=None, payload=None, dry_run=False,
                                  dedup_key=None, chain_depth=0, parent_run_id=None, enqueue=True,
                                  scope_mask=None) -> AiWorkflowRun:
    """Create (and commit) a run of `workflow`, acting as the workflow
    owner whatever `run_as_user_id` says. `scope_mask` is the API-key
    scope of the triggering credential (None: unrestricted), ANDed into
    the run's permissions. A run whose owner cannot act or access the
    entity, or whose entity is outside the workflow customer scope, is
    recorded as a minimal `skipped` run and never executes."""
    run_as_user_id = _owner_identity(workflow, run_as_user_id)
    denial = ai_workflows_engine_access_denial(workflow, run_as_user_id, entity_type, entity_id, scope_mask)
    return ai_workflows_engine_insert_run(
        workflow, trigger_type, denial=denial, triggered_by_user_id=triggered_by_user_id, entity_type=entity_type,
        entity_id=entity_id, sub_entity=sub_entity, payload=payload, dry_run=dry_run, dedup_key=dedup_key,
        chain_depth=chain_depth, parent_run_id=parent_run_id, enqueue=enqueue, scope_mask=scope_mask)


def ai_workflows_engine_insert_run(workflow, trigger_type, *, denial=None, triggered_by_user_id=None,
                                   entity_type=None, entity_id=None, sub_entity=None, payload=None, dry_run=False,
                                   dedup_key=None, chain_depth=0, parent_run_id=None, enqueue=True,
                                   scope_mask=None, replay=None, tested_node=None) -> AiWorkflowRun:
    """Insert (and commit) the run once `ai_workflows_engine_access_denial`
    returned `denial`: the trigger path checks access before taking the
    workflow lock (the check may commit), then inserts under the lock.
    `replay` (`{step_id, node_id, context}`) starts the run at that node
    with that context instead of at the trigger; with `tested_node` the
    run executes that node definition alone."""
    common = {
        'run_as_user_id': workflow.owner_id,
        'triggered_by_user_id': triggered_by_user_id,
        'entity_type': entity_type,
        'entity_id': entity_id,
        'sub_entity': sub_entity,
        'payload': payload,
        'dry_run': dry_run,
        'chain_depth': chain_depth,
        'parent_run_id': parent_run_id,
        'scope_mask': scope_mask,
    }
    if denial is not None:
        run = _new_run(workflow, trigger_type, dedup_key=None, minimal=True, **common)
        run.status = RUN_SKIPPED
        run.error = str(denial)[:_MAX_ERROR]
        run.finished_at = ai_workflows_db_utcnow()
        ai_workflows_db_add(run)
        ai_workflows_db_commit()
        logger.info(f'AI workflow #{workflow.id} run {run.uuid} refused: {denial}')
        return run
    run = _new_run(workflow, trigger_type, dedup_key=dedup_key, **common)
    if entity_type and entity_id is not None:
        run.customer_id = ai_workflows_entities_customer(entity_type, entity_id)
    run.pending_nodes = [ai_workflows_graph_trigger_id(workflow.graph)]
    if replay is not None:
        run.context = replay['context']
        run.pending_nodes = [replay['node_id']]
        run.replayed_from_step_id = replay['step_id']
    if tested_node is not None:
        run.definition_snapshot = {**run.definition_snapshot,
                                   'graph': ai_workflows_tools_json_safe({'nodes': [tested_node], 'edges': []})}
        run.tested_node_id = tested_node['id']
    ai_workflows_db_add(run)
    ai_workflows_db_commit()
    if enqueue:
        _enqueue(run.id)
    return run


def ai_workflows_engine_skip_run(workflow, trigger_type, reason, **_trigger):
    """Count (and commit) a trigger that was suppressed (dedup, rate
    limit, chain depth, run already active) on the workflow: no run row
    is written, so a trigger storm cannot fill the run table. `reason`
    is kept (64 characters) as the workflow's `last_skip_reason`.
    Returns None."""
    ai_workflows_db_count_skip(workflow.id, reason, ai_workflows_db_utcnow())
    ai_workflows_db_commit()
    logger.info(f'AI workflow #{workflow.id} {trigger_type} trigger suppressed: {reason}')


# ---- Replaying -----------------------------------------------------------------------

# Steps whose output the run context held (see `_record_node`, `_park`,
# `ai_workflows_engine_resume_wait`)
_CONTEXT_STEP_STATUSES = (STEP_SUCCEEDED, STEP_WAITING, STEP_RESUMED)


def ai_workflows_engine_replay_context(source, steps, step) -> dict:
    """The context `step` (of the run `source`) saw: the trigger and the
    entity snapshot of the source run, the outputs of the nodes recorded
    before the step (the latest one per node) and the variables set
    before it. `steps` are the steps of `source`, in `seq` order."""
    context = source.context or {}
    nodes = {}
    variables = {}
    last = None
    for earlier in steps:
        if earlier.seq >= step.seq:
            break
        error_port = earlier.status == STEP_FAILED and earlier.port == PORT_ERROR
        if earlier.status not in _CONTEXT_STEP_STATUSES and not error_port:
            continue
        nodes[earlier.node_id] = {'output': earlier.output, 'port': earlier.port}
        last = earlier.node_id
        if earlier.node_type == 'set_variables' and earlier.status == STEP_SUCCEEDED \
                and isinstance(earlier.output, dict):
            variables.update(earlier.output)
    trigger = context.get('trigger')
    if not isinstance(trigger, dict):
        trigger = ai_workflows_context_initial(source.trigger_type, source.trigger_payload, source.entity_type,
                                               source.entity_id, source.sub_entity)['trigger']
    replayed = {
        'trigger': trigger,
        'entity': context.get('entity') if isinstance(context.get('entity'), dict) else {},
        'nodes': nodes,
        'vars': variables,
        'replay': {'run_uuid': str(source.uuid) if source.uuid else None, 'step_id': step.id,
                   'node_id': step.node_id},
    }
    if last is not None:
        replayed['last'] = last
    return ai_workflows_tools_json_safe(replayed)


def ai_workflows_engine_replay_step(workflow, source, step, steps, *, triggered_by_user_id, dry_run=False,
                                    scope_mask=None, enqueue=True) -> AiWorkflowRun:
    """Create (and commit) a run of the current definition of `workflow`
    that replays the event `step` processed in `source`: it starts at the
    step's node with the context that step saw, acting as the workflow
    owner like any run (refused — a `skipped` run — when the owner lost
    access to the entity). The caller checked the node still exists."""
    run_as_user_id = workflow.owner_id
    denial = ai_workflows_engine_access_denial(workflow, run_as_user_id, source.entity_type, source.entity_id,
                                               scope_mask)
    replay = {'step_id': step.id, 'node_id': step.node_id,
              'context': ai_workflows_engine_replay_context(source, steps, step)}
    return ai_workflows_engine_insert_run(
        workflow, source.trigger_type, denial=denial, triggered_by_user_id=triggered_by_user_id,
        entity_type=source.entity_type, entity_id=source.entity_id, sub_entity=source.sub_entity,
        payload=source.trigger_payload, dry_run=dry_run, chain_depth=source.chain_depth or 0,
        parent_run_id=source.parent_run_id, enqueue=enqueue, scope_mask=scope_mask, replay=replay)


def ai_workflows_engine_test_context(trigger_type, payload, entity_type, entity_id, nodes=None,
                                     variables=None) -> dict:
    """The context of a node test that does not start from an event:
    a trigger, the entity snapshot, and the outputs of the nodes upstream
    and the variables the tester supplied."""
    context = ai_workflows_context_initial(trigger_type, payload, entity_type, entity_id, None)
    if entity_type and entity_id is not None:
        context['entity'] = ai_workflows_entities_snapshot(entity_type, entity_id) or {}
    context['nodes'] = nodes or {}
    context['vars'] = variables or {}
    return ai_workflows_tools_json_safe(context)


def ai_workflows_engine_test_node(workflow, node, context, *, trigger_type, triggered_by_user_id,
                                  entity_type=None, entity_id=None, sub_entity=None, payload=None, dry_run=False,
                                  source_step_id=None, scope_mask=None, enqueue=True) -> AiWorkflowRun:
    """Create (and commit) a run that executes `node` (a definition the
    caller validated, possibly not saved yet) on its own with `context`,
    acting as the workflow owner like any run (refused — a `skipped` run
    — when the owner cannot access the entity). It stops after the node."""
    denial = ai_workflows_engine_access_denial(workflow, workflow.owner_id, entity_type, entity_id, scope_mask)
    replay = {'step_id': source_step_id, 'node_id': node['id'], 'context': context}
    return ai_workflows_engine_insert_run(
        workflow, trigger_type, denial=denial, triggered_by_user_id=triggered_by_user_id,
        entity_type=entity_type, entity_id=entity_id, sub_entity=sub_entity, payload=payload, dry_run=dry_run,
        enqueue=enqueue, scope_mask=scope_mask, replay=replay, tested_node=node)


# ---- Executing ------------------------------------------------------------------------

def _finish(run, status, error=None):
    run.status = status
    run.error = error[:_MAX_ERROR] if error else None
    run.pending_nodes = []
    run.waiting_node_id = None
    run.finished_at = ai_workflows_db_utcnow()


def _close_waits(run, user_id=None):
    """Cancel the pending waits of a finished run and expire the
    questions linked to them; returns the suggestions to re-emit."""
    expired = []
    now = ai_workflows_db_utcnow()
    for wait in ai_workflows_db_pending_waits(run.id):
        wait.status = WAIT_CANCELLED
        wait.resolved_at = now
        wait.resolved_by_id = user_id
        expired.extend(_expire_suggestions(wait))
    return expired


def _expire_suggestions(wait) -> list:
    suggestions = ai_workflows_db_open_suggestions_for_wait(wait.id)
    now = ai_workflows_db_utcnow()
    for suggestion in suggestions:
        suggestion.status = SUGGESTION_EXPIRED
        suggestion.resolved_at = now
    return suggestions


def _emit_all(suggestions):
    for suggestion in suggestions:
        ai_workflows_suggestions_emit(suggestion, 'updated')


def _run_payload(run) -> dict:
    return {
        'id': run.id,
        'uuid': str(run.uuid) if run.uuid else None,
        'workflow_id': run.workflow_id,
        'workflow_name': run.workflow_name,
        'workflow_version': run.workflow_version,
        'status': run.status,
        'trigger_type': run.trigger_type,
        'entity_type': run.entity_type,
        'entity_id': run.entity_id,
        'sub_entity': run.sub_entity,
        'error': run.error,
        'tokens_used': run.tokens_used,
        'step_count': run.step_count,
        'chain_depth': run.chain_depth,
        'parent_run_id': run.parent_run_id,
        'started_at': _iso(run.started_at),
        'finished_at': _iso(run.finished_at),
    }


def _publish_complete(run):
    """`on_postload_ai_workflow_run_complete`, inside the run's chain so
    the workflows it triggers count one level deeper. Not for dry runs
    nor node tests."""
    from app.iris_engine.module_handler.module_handler import call_modules_hook

    if run.is_dry_run or run.tested_node_id:
        return
    try:
        with ai_workflows_identity_chain(run):
            call_modules_hook('on_postload_ai_workflow_run_complete', data=_run_payload(run),
                              caseid=run.entity_id if run.entity_type == ENTITY_CASE else None)
    except Exception:
        logger.exception(f'on_postload_ai_workflow_run_complete failed for AI workflow run {run.uuid}')


def _prepared_wait(run, spec):
    """The wait a node committed before it returned (`spec['wait_id']`,
    see `nodes._prepare_wait`), locked after the run; None if none."""
    wait_id = spec.get('wait_id')
    if wait_id is None:
        return None
    wait = ai_workflows_db_get_wait(wait_id, lock=True)
    if wait is None or wait.run_id != run.id:
        return None
    return wait


def _park(run, step, node, result, prepared=None):
    """Create the wait of a parking node (and the question linked to it),
    or adopt the one the node prepared. A prepared wait already resolved
    (an early callback resumed the branch) parks nothing."""
    spec = result.wait
    if prepared is not None:
        step.output = {**(step.output or {}), 'wait_uuid': str(prepared.uuid),
                       'expires_at': _iso(prepared.expires_at)}
        if prepared.status != WAIT_PENDING:
            step.output['resolved_before_park'] = True
            return prepared
        step.status = STEP_WAITING
        run.waiting_node_id = node['id']
        return prepared
    minutes = int(spec.get('minutes') or 60)
    wait = AiWorkflowWait(
        uuid=spec.get('uuid') or uuid.uuid4(),
        run_id=run.id,
        node_id=node['id'],
        kind=spec.get('kind'),
        token_hash=spec.get('token_hash'),
        status=WAIT_PENDING,
        expires_at=ai_workflows_db_utcnow() + datetime.timedelta(minutes=minutes),
    )
    ai_workflows_db_add(wait)
    step.status = STEP_WAITING
    run.waiting_node_id = node['id']
    if spec.get('suggestion'):
        # Commits the step and the run as well
        suggestion = ai_workflows_suggestions_create(run, step, **spec['suggestion'], wait_id=wait.id)
        wait.suggestion_id = suggestion.id
        step.output = {**(step.output or {}), 'suggestion_id': suggestion.id}
    step.output = {**(step.output or {}), 'wait_uuid': str(wait.uuid),
                   'expires_at': _iso(wait.expires_at)}
    return wait


def _fail_run(run_id, error, step_id=None):
    """Fail an active run (and the step `step_id` when it did not settle)
    after the engine itself failed or the run lost its identity / access.
    Starts from a clean session; commits."""
    ai_workflows_db_rollback()
    run = ai_workflows_db_get_run(run_id, lock=True)
    if run is None:
        ai_workflows_db_commit()
        return
    step = ai_workflows_db_get_step(step_id) if step_id is not None else None
    if step is not None and step.status in (STEP_RUNNING, STEP_WAITING):
        step.status = STEP_FAILED
        step.error = str(error)[:_MAX_ERROR]
        step.ended_at = ai_workflows_db_utcnow()
    expired = []
    alive = run.status in RUN_ACTIVE_STATUSES
    if alive:
        _finish(run, RUN_FAILED, error)
        expired = _close_waits(run)
    ai_workflows_db_commit()
    if alive:
        _emit_all(expired)
        _publish_complete(run)


def _remember_keys(run, resolver):
    """Keep the keystore names the run resolved (`used_key_names`)."""
    used = set(getattr(resolver, 'used', None) or ())
    known = set(run.used_key_names or [])
    if used - known:
        run.used_key_names = sorted(known | used)


def _execute_node(run_id, step_id, node):
    """Execute one popped node and record the outcome. Returns False
    when the run must stop."""
    run = ai_workflows_db_get_run(run_id)
    step = ai_workflows_db_get_step(step_id)
    # Keys resolve as the run-as user, i.e. the workflow owner
    resolver = ai_workflows_keystore_resolver(run.run_as_user_id)
    ctx = NodeContext(run, step, node, resolver)
    result = None
    error = None
    try:
        with ai_workflows_identity(run.run_as_user_id, run):
            result = ai_workflows_nodes_execute(ctx, node)
    except AiWorkflowIdentityError as e:
        # Not a node failure the graph can route around: the run has no
        # one left to act as
        ai_workflows_db_settle_session()
        _fail_run(run_id, f'The run cannot act as its user: {e}', step_id)
        return False
    except Exception as e:
        logger.info(f'AI workflow run #{run_id} node {node.get("id")} failed: {e}', exc_info=True)
        error = str(e) or e.__class__.__name__
    if ai_workflows_db_settle_session() and error is None:
        error = 'The node results could not be saved'
        result = None

    try:
        return _record_node(run_id, step_id, node, ctx, resolver, result, error)
    except Exception as e:
        # A node that ran but could not be recorded must not leave the run
        # looking healthy (nor be executed again)
        logger.exception(f'AI workflow run #{run_id}: recording node {node.get("id")} failed')
        _fail_run(run_id, f'Recording node {node.get("label") or node.get("id")} failed: '
                          f'{e.__class__.__name__}', step_id)
        return False


def _record_node(run_id, step_id, node, ctx, resolver, result, error):
    run = ai_workflows_db_get_run(run_id, lock=True)
    step = ai_workflows_db_get_step(step_id)
    if run is None or step is None or step.status != STEP_RUNNING:
        # Recovery already settled this step (and the run): keep its verdict
        ai_workflows_db_commit()
        return False
    step.ended_at = ai_workflows_db_utcnow()
    step.tokens_used = ctx.tokens
    mask = resolver.mask
    graph = _graph(run)
    node_id = node['id']
    # A run cancelled or failed meanwhile only gets the step recorded
    alive = run.status in RUN_ACTIVE_STATUSES
    if alive:
        _remember_keys(run, resolver)

    if error is not None:
        error = str(mask(error))[:_MAX_ERROR]
        step.status = STEP_FAILED
        step.error = error
        has_error_port = PORT_ERROR in NODE_PORTS.get(node.get('type'), ())
        targets = ai_workflows_graph_targets(graph, node_id, PORT_ERROR) if has_error_port else []
        if targets and alive:
            step.port = PORT_ERROR
            step.output = {'error': error}
            _store_node(run, node_id, {'error': error}, PORT_ERROR)
            _push(run, graph, node_id, PORT_ERROR)
            ai_workflows_db_commit()
            return True
        if alive:
            _finish(run, RUN_FAILED, f'{node.get("label") or node_id}: {error}')
            expired = _close_waits(run)
            ai_workflows_db_commit()
            _emit_all(expired)
            _publish_complete(run)
        else:
            ai_workflows_db_commit()
        return False

    output = mask(ai_workflows_tools_json_safe(result.output if result.output is not None else {}))
    step.input = mask(ai_workflows_tools_json_safe(result.input)) if result.input is not None else None
    step.output = output
    step.port = result.port
    step.status = STEP_SUCCEEDED

    if not alive:
        ai_workflows_db_commit()
        return False

    updates = {k: mask(ai_workflows_tools_json_safe(v)) for k, v in (result.context_updates or {}).items()
               if k in ('entity', 'vars')}
    if updates:
        _set_context(run, **updates)
    prepared = _prepared_wait(run, result.wait) if result.wait else None
    if prepared is None or prepared.status == WAIT_PENDING:
        _store_node(run, node_id, output, result.port)
    else:
        # An early callback already stored the resumed output (and port),
        # without the response the node had not returned yet
        stored = dict(((run.context or {}).get('nodes') or {}).get(node_id) or {})
        resumed = stored.get('output') if isinstance(stored.get('output'), dict) else {}
        merged = {**resumed, **{k: v for k, v in output.items() if resumed.get(k) is None}}
        _store_node(run, node_id, merged, stored.get('port'))

    if result.stop_status:
        _finish(run, result.stop_status, result.stop_reason if result.stop_status == RUN_FAILED else None)
        if result.stop_status != RUN_FAILED and result.stop_reason:
            _set_context(run, stop_reason=result.stop_reason)
        expired = _close_waits(run)
        ai_workflows_db_commit()
        _emit_all(expired)
        _publish_complete(run)
        return False

    if result.wait:
        _park(run, step, node, result, prepared)
    else:
        _push(run, graph, node_id, result.port)
    ai_workflows_db_commit()
    return True


def _access_lost(run_id):
    """Why the run may not execute its next node (its run-as user lost
    access to the entity), or None. Read before the run is locked: the
    access check may commit."""
    run = ai_workflows_db_get_run(run_id)
    if run is None or not run.entity_type or run.entity_id is None:
        return None
    if not ai_workflows_entities_scope_allows(run.entity_type, run.scope_mask) \
            or not ai_workflows_entities_user_can_access(run.run_as_user_id, run.entity_type, run.entity_id):
        return f'Access to {run.entity_type} #{run.entity_id} lost by the user the run acts as'
    return None


def _pop(run_id):
    """Pop the next node under the run lock and commit its step row.
    Returns (step_id, node) or None when there is nothing to do."""
    run = ai_workflows_db_get_run(run_id, lock=True)
    if run is None or run.status != RUN_RUNNING or not run.pending_nodes:
        ai_workflows_db_commit()
        return None
    pending = list(run.pending_nodes)
    node_id = pending.pop(0)
    run.pending_nodes = pending
    node = ai_workflows_graph_nodes(_graph(run)).get(node_id)
    if node is None:
        _finish(run, RUN_FAILED, f'Unknown node {node_id}')
        expired = _close_waits(run)
        ai_workflows_db_commit()
        _emit_all(expired)
        _publish_complete(run)
        return None
    max_steps = _config('AI_WORKFLOWS_MAX_STEPS_PER_RUN', 100)
    if (run.step_count or 0) >= max_steps:
        _finish(run, RUN_FAILED, f'Step limit reached ({max_steps} steps)')
        expired = _close_waits(run)
        ai_workflows_db_commit()
        _emit_all(expired)
        _publish_complete(run)
        return None
    run.step_count = (run.step_count or 0) + 1
    run.executing_since = ai_workflows_db_utcnow()
    step = AiWorkflowRunStep(
        run_id=run.id,
        seq=ai_workflows_db_next_step_seq(run.id),
        node_id=node_id,
        node_type=node.get('type'),
        node_label=(node.get('label') or None),
        status=STEP_RUNNING,
        tokens_used=0,
        started_at=ai_workflows_db_utcnow(),
    )
    ai_workflows_db_add(step)
    ai_workflows_db_commit()
    return step.id, node


def _settle_run(run_id):
    """Release the run; decide waiting / succeeded / re-enqueue."""
    run = ai_workflows_db_get_run(run_id, lock=True)
    if run is None:
        ai_workflows_db_commit()
        return
    run.is_executing = False
    run.executing_since = None
    requeue = False
    finished = False
    if run.status == RUN_RUNNING:
        if run.pending_nodes:
            requeue = True
        elif ai_workflows_db_pending_waits(run.id):
            run.status = RUN_WAITING
        else:
            _finish(run, RUN_SUCCEEDED)
            finished = True
    elif run.status == RUN_WAITING and run.pending_nodes:
        run.status = RUN_RUNNING
        requeue = True
    ai_workflows_db_commit()
    if requeue:
        _enqueue(run_id)
    if finished:
        _publish_complete(run)


def ai_workflows_engine_step(run_id):
    """Worker entry point: execute the pending nodes of a run."""
    run = ai_workflows_db_get_run(run_id, lock=True)
    if run is None or run.status != RUN_RUNNING or run.is_executing:
        # Gone, finished, parked, or another worker has it (and re-reads the queue)
        ai_workflows_db_commit()
        return
    run.is_executing = True
    run.executing_since = ai_workflows_db_utcnow()
    ai_workflows_db_commit()
    step_id = None
    try:
        for _index in range(_NODES_PER_TASK):
            lost = _access_lost(run_id)
            if lost is not None:
                _fail_run(run_id, lost)
                break
            popped = _pop(run_id)
            if popped is None:
                break
            step_id = popped[0]
            keep_going = _execute_node(run_id, *popped)
            step_id = None
            if not keep_going:
                break
    except Exception as e:
        logger.exception(f'AI workflow run #{run_id} step failed')
        try:
            _fail_run(run_id, f'The workflow engine failed: {e.__class__.__name__}', step_id)
        except Exception:
            logger.exception(f'AI workflow run #{run_id} could not be marked failed')
            ai_workflows_db_rollback()
    finally:
        _settle_run(run_id)


def ai_workflows_engine_recover_stale(run, before=None):
    """A worker died while executing `run`: its in-flight node is failed
    (never re-executed), then the run goes on. With `before`, a run whose
    worker sent a heartbeat since is left alone."""
    run = ai_workflows_db_get_run(run.id, lock=True)
    if run is None or not run.is_executing or (
            before is not None and run.executing_since is not None and run.executing_since >= before):
        ai_workflows_db_commit()
        return
    lost = ai_workflows_db_steps_with_status(run.id, STEP_RUNNING)
    now = ai_workflows_db_utcnow()
    for step in lost:
        step.status = STEP_FAILED
        step.error = 'The worker executing this node stopped'
        step.ended_at = now
    if lost and run.status in RUN_ACTIVE_STATUSES:
        _finish(run, RUN_FAILED, f'The worker executing node {lost[0].node_id} stopped')
        expired = _close_waits(run)
    else:
        expired = []
    run.is_executing = False
    run.executing_since = None
    ai_workflows_db_commit()
    _emit_all(expired)
    if lost:
        _publish_complete(run)
    elif run.status == RUN_RUNNING:
        _enqueue(run.id)


def ai_workflows_engine_requeue(run):
    """Re-enqueue a run that lost its queue message; stamped so the tick
    does not enqueue it again before the stuck threshold."""
    ai_workflows_db_mark_requeued(run.id, ai_workflows_db_utcnow())
    _enqueue(run.id)


# ---- Waits ----------------------------------------------------------------------------

def _resume_output(run, wait, payload, port) -> dict:
    previous = ((run.context or {}).get('nodes') or {}).get(wait.node_id) or {}
    initial = previous.get('output') if isinstance(previous.get('output'), dict) else {}
    initial = {k: v for k, v in initial.items() if k not in ('wait_uuid', 'expires_at', 'suggestion_id')}
    if wait.kind == WAIT_CALLBACK:
        if port == PORT_TIMEOUT:
            return {**initial, 'payload': None, 'timed_out': True}
        return {'status_code': initial.get('status_code'), 'body': initial.get('body'), 'payload': payload,
                'received_at': _iso(ai_workflows_db_utcnow())}
    if wait.kind == WAIT_USER_INPUT:
        output = dict(payload) if isinstance(payload, dict) else {}
        output.setdefault('answer', None)
        output.setdefault('answered_by', None)
        if port == PORT_TIMEOUT:
            output['timed_out'] = True
        return output
    return {}


def _default_port(kind) -> str:
    return PORT_ANSWERED if kind == WAIT_USER_INPUT else PORT_OUT


def ai_workflows_engine_lock_run_then_wait(wait_id=None, *, wait_uuid=None) -> tuple:
    """Lock a wait the way every path does — its run first, then the wait
    — and return `(run, wait)` reloaded under the locks ((None, None) when
    the wait does not exist). The caller re-checks `wait.status`, then
    resolves it (`ai_workflows_engine_resume_wait`) or commits."""
    if wait_id is not None:
        wait = ai_workflows_db_get_wait(wait_id)
    elif wait_uuid is not None:
        wait = ai_workflows_db_get_wait_by_uuid(wait_uuid)
    else:
        wait = None
    if wait is None:
        return None, None
    run = ai_workflows_db_get_run(wait.run_id, lock=True)
    wait = ai_workflows_db_get_wait(wait.id, lock=True)
    return run, wait


def ai_workflows_engine_resume_wait(wait, payload, *, resolved_by_id=None, source_ip=None,
                                    port=None) -> AiWorkflowRun:
    """Resolve a pending wait (callback received, question answered,
    suggestion accepted, wait expired) and continue its branch. The
    caller locked the run then the wait
    (`ai_workflows_engine_lock_run_then_wait`); this commits. The
    resumed branch re-checks the run's access before its next node."""
    run = ai_workflows_db_get_run(wait.run_id, lock=True)
    if wait.status != WAIT_PENDING:
        return run
    now = ai_workflows_db_utcnow()
    node = ai_workflows_graph_nodes(_graph(run)).get(wait.node_id) or {'id': wait.node_id, 'type': None}
    default = _default_port(wait.kind)
    if port not in NODE_PORTS.get(node.get('type'), ()):
        port = default
    timed_out = port == PORT_TIMEOUT

    safe_payload = ai_workflows_tools_json_safe(payload) if payload is not None else None
    wait.status = WAIT_EXPIRED if timed_out else WAIT_RESOLVED
    wait.resolved_payload = safe_payload
    wait.resolved_by_id = resolved_by_id
    wait.resolved_at = now
    wait.source_ip = (str(source_ip)[:64] if source_ip else None)
    wait.payload_sha256 = _sha256(safe_payload) if safe_payload is not None else None
    expired = _expire_suggestions(wait) if timed_out else []

    if run.status not in RUN_ACTIVE_STATUSES:
        ai_workflows_db_commit()
        _emit_all(expired)
        return run

    output = _resume_output(run, wait, safe_payload, port)
    step = AiWorkflowRunStep(
        run_id=run.id,
        seq=ai_workflows_db_next_step_seq(run.id),
        node_id=wait.node_id,
        node_type=node.get('type') or wait.kind,
        node_label=node.get('label') or None,
        status=STEP_RESUMED,
        input={'wait_uuid': str(wait.uuid), 'kind': wait.kind, 'resolved_by_id': resolved_by_id,
               'source_ip': wait.source_ip, 'payload_sha256': wait.payload_sha256},
        output=output,
        port=port,
        tokens_used=0,
        started_at=now,
        ended_at=now,
    )
    ai_workflows_db_add(step)
    run.step_count = (run.step_count or 0) + 1
    _store_node(run, wait.node_id, output, port)
    _push(run, _graph(run), wait.node_id, port)
    others = [w for w in ai_workflows_db_pending_waits(run.id) if w.id != wait.id]
    run.waiting_node_id = others[0].node_id if others else None
    finished = False
    if run.pending_nodes:
        run.status = RUN_RUNNING
    elif not others and not run.is_executing:
        _finish(run, RUN_SUCCEEDED)
        finished = True
    ai_workflows_db_commit()
    _emit_all(expired)
    if finished:
        _publish_complete(run)
    elif run.status == RUN_RUNNING and not run.is_executing:
        _enqueue(run.id)
    return run


def ai_workflows_engine_expire_wait(wait):
    """The tick found `wait` past its expiry: a delay completes, other
    waits leave through `timeout`."""
    port = PORT_OUT if wait.kind == WAIT_DELAY else PORT_TIMEOUT
    return ai_workflows_engine_resume_wait(wait, None, port=port)


# ---- Cancelling -----------------------------------------------------------------------

def ai_workflows_engine_cancel_run(run, user_id, reason=None) -> AiWorkflowRun:
    """Cancel an active run: nothing pending executes, waits are
    cancelled and their questions expired. A node executing right now
    finishes, but the run does not continue. Commits."""
    run = ai_workflows_db_get_run(run.id, lock=True) or run
    if run.status not in RUN_ACTIVE_STATUSES:
        ai_workflows_db_commit()
        return run
    _finish(run, RUN_CANCELLED)
    run.error = (reason or 'Cancelled')[:_MAX_ERROR]
    expired = _close_waits(run, user_id)
    ai_workflows_db_commit()
    _emit_all(expired)
    _publish_complete(run)
    return run
